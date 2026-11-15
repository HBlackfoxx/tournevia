"""Valhalla adapter. No error/omitted cell can be silently turned into unassignment."""
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import http.client
from itertools import islice
import json
import logging
import math
import re
import threading
import time
from urllib.parse import urlsplit

from app.fleet import Location
from app.planning import Leg, Policy, Truck


class EngineError(RuntimeError):
    pass


class MatrixSelfArcError(EngineError):
    def __init__(self, point_index: int):
        self.point_index = point_index
        super().__init__(f'Valhalla returned a nonzero self-arc at point {point_index}; calculation rejected.')


class SnapError(EngineError):
    """A final road path ends too far from one of its reviewed endpoints."""
    def __init__(self, endpoint: str, limit_m: int):
        self.endpoint = endpoint
        super().__init__(f'Road path snaps more than {limit_m} m from a reviewed service point; correct the pin.')


@dataclass
class Deadline:
    end: float

    @classmethod
    def after(cls, seconds: float):
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        left = self.end - time.monotonic()
        if left <= 0:
            raise EngineError('Calculation deadline exceeded.')
        return left


# One routing request may take this long; a timed-out request is retried once only if
# the deadline still leaves this much and the margin for the retry.
REQUEST_SECONDS = 10
RETRY_MARGIN_SECONDS = 5


@dataclass
class Matrix:
    durations: list[list[int | None]]
    distances: list[list[int | None]]
    # Points whose pin has no road usable by this profile within the snap limit.
    no_road: frozenset[int] = frozenset()


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(value)))


def coordinates(point: Location) -> tuple[float, float]:
    return point.longitude, point.latitude


def costing(truck: Truck, policy: Policy) -> dict:
    return {'height': truck.height_mm/1000, 'width': truck.width_mm/1000,
            'length': truck.length_mm/1000, 'weight': truck.gross_weight_kg/1000,
            'axle_load': truck.axle_load_kg/1000, 'axle_count': truck.axle_count,
            'use_tolls': 0 if policy == 'TRUCK_PREFER_NO_TOLLS' else 0.5,
            'use_highways': 0 if policy == 'TRUCK_PREFER_NO_MOTORWAYS' else 0.5,
            'ignore_restrictions': False, 'ignore_oneways': False, 'ignore_access': False,
            'hgv_no_access_penalty': 43200, 'speed_types': []}


def whole(value, factor: int = 1) -> int:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise EngineError('Invalid engine distance or duration.')
    # Round away binary noise first: 2.007 km * 1000 is 2006.9999999999998, not 2007 m.
    return math.ceil(round(value * factor, 6))


def decode_shape(encoded: str) -> list[tuple[float, float]]:
    if not isinstance(encoded, str):
        raise EngineError('Expected a polyline6 road shape.')
    values, position, latitude, longitude = [], 0, 0, 0
    while position < len(encoded):
        pair = []
        for _ in range(2):
            number = shift = 0
            while True:
                if position >= len(encoded) or shift > 30:
                    raise EngineError('Truncated or overflowing polyline6.')
                char = ord(encoded[position])-63
                position += 1
                if not 0 <= char <= 63:
                    raise EngineError('Invalid polyline6 character.')
                number |= (char & 31) << shift
                shift += 5
                if char < 32:
                    break
            pair.append(~(number >> 1) if number & 1 else number >> 1)
        latitude += pair[0]; longitude += pair[1]
        values.append((longitude/1_000_000, latitude/1_000_000))
    return values


def engine_rejection(action: str, status: int, content: bytes) -> EngineError:
    """Name only Valhalla's numeric error code; its text can echo request details.

    The HTTP status and parsed body stay available to diagnostics as
    ``engine_status`` and ``engine_detail``.
    """
    try:
        detail = json.loads(content)
    except (ValueError, TypeError):
        detail = None
    code = detail.get('error_code') if isinstance(detail, dict) else None
    rejection = EngineError(f'Valhalla {action} failed (HTTP {status}'
                            f'{f", engine error {code}" if type(code) is int else ""}); no route result accepted.')
    rejection.engine_status = status
    rejection.engine_detail = detail if isinstance(detail, dict) else None
    return rejection


def failure_detail(error: BaseException) -> str:
    """Class name and errno only (e.g. ConnectionRefusedError errno 111); never the request."""
    code = getattr(error, 'errno', None)
    return type(error).__name__ + (f' errno {code}' if isinstance(code, int) else '')


# 250 delivery points plus where routes start and end: the main depot and two places per vehicle at most.
MAX_MATRIX_POINTS = 301
MATRIX_BATCH_SIZE = 48


def matrix_tiles(count: int, size: int, time_bounds=None, anchors: int = 1):
    """Omit only rectangles ruled out by nonnegative travel and hard windows.

    Bounds are (earliest departure after service, latest service start), ordered
    by the latter. The first `anchors` indexes are where routes start and end (the
    depot, other depots and places): never bounded, they always retain engine connections.
    """
    cuts = ([] if time_bounds is None else
            [i for i in range(anchors+1, count) if time_bounds[i][1] != time_bounds[i-1][1]]) + [count]
    def chunks(start, end):
        if count <= 2*size:
            return [(i, min(i+size, end)) for i in range(start, end, size)]
        # Several request waves: balance their axes instead of leaving one tiny
        # remainder behind maximum-size requests. Keep the same number of chunks
        # and window-group boundaries, reducing peak per-request search work.
        parts = (end-start+size-1)//size
        width, extra = divmod(end-start, parts)
        result = []
        for part in range(parts):
            right = start+width+(part < extra)
            result.append((start, right))
            start = right
        return result

    ranges, start = [], 0
    for end in cuts:
        ranges.extend(chunks(start, end))
        start = end
    if len(ranges) > (count+size-1)//size:
        # Irregular customer windows must not turn bounded tiles into hundreds
        # of tiny requests. Keep the ordinary tile count when groups fragment.
        ranges = chunks(0, count)
    for source, source_end in ranges:
        for target, target_end in ranges:
            if time_bounds is not None and source >= anchors and source != target and target_end > max(anchors, target):
                earliest = min(time_bounds[i][0] for i in range(source, source_end))
                latest = max(time_bounds[j][1] for j in range(max(anchors, target), target_end))
                if earliest > latest:  # Equality can still permit zero-duration travel.
                    if target >= anchors:
                        continue
                    target_end = anchors  # Keep the columns of returns to route ends.
            yield source, target, source_end, target_end


def check_engine_warnings(data: dict, action: str, payload: dict) -> None:
    """Only the pinned CostMatrix's explicit second-pass notice is supported.

    These are heuristic matrix estimates, not accepted road paths. Null cells
    remain forbidden; every chosen leg still requires warning-free routing and
    independent validation. Clamping, unknown warnings and route warnings fail.
    """
    warnings = data.get('warnings')
    if warnings is None or warnings == []:
        return
    if (action != 'sources_to_targets' or data.get('algorithm') != 'costmatrix'
            or not isinstance(warnings, list) or len(warnings) != 1):
        raise EngineError('Valhalla returned an unsupported warning.')
    notice = warnings[0]
    if (not isinstance(notice, dict) or set(notice) != {'code', 'text'}
            or type(notice['code']) is not int or notice['code'] != 400
            or not isinstance(notice['text'], str)):
        raise EngineError('Valhalla returned an unsupported warning.')
    match = re.fullmatch(r'CostMatrix turned off destination-only on a second pass for connections: ([0-9]{1,5}(?:,[0-9]{1,5})*)', notice['text'])
    if not match:
        raise EngineError('Valhalla returned an unsupported second-pass notice.')
    indexes = [int(value) for value in match[1].split(',')]
    cells = len(payload.get('sources', [])) * len(payload.get('targets', []))
    if indexes != sorted(set(indexes)) or indexes[-1] >= cells:
        raise EngineError('Valhalla second-pass connection indexes are invalid.')
    logging.getLogger(__name__).info('CostMatrix second pass searched %s connections.', len(indexes))


def parse_matrix(data: dict, size: int, target_size: int | None = None, *, source_offset: int = 0,
                 target_offset: int = 0) -> Matrix:
    target_size = size if target_size is None else target_size
    try:
        durations, distances = data['sources_to_targets']['durations'], data['sources_to_targets']['distances']
        if data['units'] != 'kilometers' or len(durations) != size or len(distances) != size:
            raise ValueError()
        output_t, output_d = [], []
        for i in range(size):
            if len(durations[i]) != target_size or len(distances[i]) != target_size:
                raise ValueError()
            row_t, row_d = [], []
            for j in range(target_size):
                t, d = durations[i][j], distances[i][j]
                if (t is None) != (d is None):
                    raise ValueError()
                seconds = None if t is None else whole(t)
                distance = None if d is None else whole(d, 1000)
                if source_offset+i == target_offset+j and (t != 0 or d != 0):
                    if t is None:
                        raise EngineError(f'Valhalla returned an unreachable self-arc at point {source_offset+i}; calculation rejected.')
                    raise MatrixSelfArcError(source_offset+i)
                row_t.append(seconds)
                row_d.append(distance)
            output_t.append(row_t)
            output_d.append(row_d)
        return Matrix(output_t, output_d)
    except (KeyError, TypeError, ValueError) as error:
        raise EngineError('Incomplete or unsupported Valhalla matrix response.') from error


class Valhalla:
    def __init__(self, url: str, dataset: str, *, snap_limit_m: int = 50, matrix_batch_size: int = MATRIX_BATCH_SIZE,
                 matrix_concurrency: int = 1):
        if not 1 <= snap_limit_m <= 100:
            raise ValueError('Spike snap limit must be 1–100 metres; correct pins instead of expanding silently.')
        self.url, self.dataset, self.snap_limit_m = url.rstrip('/'), dataset, snap_limit_m
        if type(matrix_batch_size) is not int or not 1 <= matrix_batch_size <= MATRIX_BATCH_SIZE:
            raise ValueError('Matrix batch size must be 1–48.')
        self.matrix_batch_size = matrix_batch_size
        if type(matrix_concurrency) is not int or not 1 <= matrix_concurrency <= 4:
            raise ValueError('Matrix concurrency must be 1–4; provision matching routing workers and resources.')
        self.matrix_concurrency = matrix_concurrency
        origin = urlsplit(self.url)
        if origin.scheme not in ('http', 'https') or not origin.hostname:
            raise ValueError('Routing service URL must be an HTTP(S) URL.')
        self.origin = origin
        # One persistent HTTP/1.1 connection per thread. A calculation can send
        # thousands of route requests; a new TCP connection for each one can
        # exhaust local ports and fail an otherwise valid calculation.
        self.connections = threading.local()

    def matrix_responses(self, locations, truck, policy, deadline, time_bounds=None, anchors=1):
        """Drain each bounded wave before feeding the routing service more work."""
        size = self.matrix_batch_size
        # Small return-to-depot rectangles go last, so they do not leave most of
        # a wave idle beside full tiles. Request ordering does not change cells.
        tasks = iter(sorted(matrix_tiles(len(locations), size, time_bounds, anchors),
                            key=lambda t: -(t[2]-t[0])*(t[3]-t[1])))
        def request(pair):
            source, target, source_end, target_end = pair
            deadline.remaining()
            sources, targets = locations[source:source_end], locations[target:target_end]
            data = self.post('sources_to_targets', {**self.base(truck, policy), 'verbose': False,
                             'sources': sources, 'targets': targets}, deadline)
            return source, target, len(sources), len(targets), data
        if self.matrix_concurrency == 1 or len(locations) <= size:
            for task in tasks:
                yield request(task)
            return
        pool = ThreadPoolExecutor(max_workers=self.matrix_concurrency, thread_name_prefix='routing-matrix')
        pending = []
        try:
            while batch := list(islice(tasks, self.matrix_concurrency)):
                pending = [pool.submit(request, task) for task in batch]
                try:
                    results = [future.result(timeout=deadline.remaining()) for future in pending]
                except TimeoutError as error:
                    raise EngineError('Calculation deadline exceeded while waiting for a matrix tile.') from error
                deadline.remaining()
                # The pinned service can queue a replacement behind a busy
                # internal worker even while another is idle. Complete and parse
                # this entire wave before submitting the next one.
                for result in results:
                    yield result
        finally:
            # Cancel queued work and join active bounded HTTP calls before leaving.
            # Parsing errors also close this generator; partial matrices never escape.
            pool.shutdown(wait=True, cancel_futures=True)

    def connection(self, timeout: float) -> http.client.HTTPConnection:
        connection = getattr(self.connections, 'current', None)
        if connection is None:
            kind = http.client.HTTPSConnection if self.origin.scheme == 'https' else http.client.HTTPConnection
            connection = self.connections.current = kind(self.origin.hostname, self.origin.port, timeout=timeout)
        connection.timeout = timeout
        if connection.sock is not None:
            connection.sock.settimeout(timeout)
        return connection

    def exchange(self, action: str, body: bytes, deadline: Deadline) -> tuple[int, bytes]:
        """Send one request on this thread's kept-alive connection; return status and body.

        Retried once on a new connection: a reused connection that the server closed
        while idle, or a request that timed out (a busy router can stall one matrix
        request) when the deadline still leaves room for a full second attempt. Every
        other failure closes the connection and propagates.
        """
        path = f"{self.origin.path.rstrip('/')}/{action}"
        for attempt in range(2):
            connection = self.connection(min(REQUEST_SECONDS, deadline.remaining()))
            reused = connection.sock is not None
            try:
                connection.request('POST', path, body, {'Content-Type': 'application/json'})
                response = connection.getresponse()
                content = bytearray()
                while chunk := response.read1(65536):
                    deadline.remaining()
                    content.extend(chunk)
                    if len(content) > 16_000_000:
                        raise EngineError('Valhalla response exceeds output budget.')
                if response.will_close:
                    connection.close()
                return response.status, bytes(content)
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                connection.close()
                if reused and attempt == 0:
                    continue
                raise
            except TimeoutError:
                connection.close()
                if attempt == 0 and deadline.end - time.monotonic() > REQUEST_SECONDS + RETRY_MARGIN_SECONDS:
                    continue
                raise
            except BaseException:
                connection.close()  # Never reuse a connection after a partial exchange.
                raise
        raise AssertionError('unreachable')

    def post(self, action: str, payload: dict, deadline: Deadline) -> dict:
        try:
            status, content = self.exchange(action, json.dumps(payload).encode(), deadline)
            if status >= 400:
                raise engine_rejection(action, status, content)
            if status != 200:
                raise ValueError('Unexpected routing service status.')
            data = json.loads(content)
        except (TimeoutError, OSError, ValueError, http.client.HTTPException) as error:
            raise EngineError(f'Valhalla {action} failed ({failure_detail(error)}); no route result accepted.') from error
        if action == 'locate' and isinstance(data, list):
            return data
        if not isinstance(data, dict) or data.get('error'):
            raise EngineError('Valhalla rejected, clamped or warned about the request.')
        check_engine_warnings(data, action, payload)
        return data

    def unsnappable(self, points: list[Location], truck: Truck, policy: Policy, deadline: Deadline) -> list[int]:
        """Indexes of points without a road usable by this truck within the snap limit.

        Uses the same location search and truck costing as matrix and route
        requests. One such point makes the engine reject a whole matrix, so the
        caller can name the affected deliveries instead of failing generically.
        """
        if not points:
            return []
        data = self.post('locate', {**self.base(truck, policy), 'verbose': False,
                                    'locations': [self.point(p) for p in points]}, deadline)
        if not isinstance(data, list) or len(data) != len(points):
            raise EngineError('Incomplete or unsupported Valhalla locate response.')
        missing = []
        for index, row in enumerate(data):
            edges = row.get('edges') if isinstance(row, dict) else ()
            if edges is not None and not isinstance(edges, list):
                raise EngineError('Incomplete or unsupported Valhalla locate response.')
            if not edges:
                missing.append(index)
        return missing

    def point(self, point: Location) -> dict:
        # Radius selects candidate roads, not the maximum permitted snap distance.
        # Nearby alternatives can differ between matrix and route. Use nearest
        # candidates while retaining the search cutoff and endpoint-distance check.
        return {'lat': point.latitude, 'lon': point.longitude, 'radius': 0,
                'search_cutoff': self.snap_limit_m, 'minimum_reachability': 0}

    def base(self, truck: Truck, policy: Policy) -> dict:
        return {'costing': 'truck', 'costing_options': {'truck': costing(truck, policy)}, 'units': 'kilometers'}

    def matrix(self, points: list[Location], truck: Truck, policy: Policy, deadline: Deadline,
               *, time_bounds: list[tuple[int, int] | None] | None = None) -> Matrix:
        n = len(points)
        if not 1 <= n <= MAX_MATRIX_POINTS:
            raise EngineError('Matrix supports 1–301 distinct points (route start and end places and up to 250 deliveries).')
        anchors = 1
        if time_bounds is not None:
            # Route start and end places lead without bounds; every delivery point has bounds.
            anchors = next((i for i, b in enumerate(time_bounds) if b is not None), len(time_bounds))
            if len(time_bounds) != n or anchors == 0 or any(b is None for b in time_bounds[anchors:]):
                raise EngineError('Matrix time bounds must cover every non-depot point.')
            order = list(range(anchors)) + sorted(range(anchors, n), key=lambda i: (time_bounds[i][1], coordinates(points[i])))
            ordered_bounds = [time_bounds[i] for i in order]
            cells = sum((end-source)*(right-target) for source,target,end,right
                        in matrix_tiles(n,self.matrix_batch_size,ordered_bounds,anchors))
            if cells < n*n:
                points = [points[i] for i in order]
                time_bounds = ordered_bounds
            else:
                time_bounds = None  # Preserve spatial grouping when no work can be omitted.
        locations = [self.point(p) for p in points]
        times, distances = [[None]*n for _ in points], [[None]*n for _ in points]
        affected = set()
        # Bounded tiles share the caller's whole-calculation deadline.
        # Fill every requested directed cell; never mirror arcs or turn a failed tile into nulls.
        with closing(self.matrix_responses(locations, truck, policy, deadline, time_bounds, anchors)) as responses:
            for source, target, source_count, target_count, data in responses:
                while True:
                    try:
                        block = parse_matrix(data,source_count,target_count,source_offset=source,target_offset=target)
                        break
                    except MatrixSelfArcError as error:
                        # The pinned CostMatrix cannot connect on complex-restriction
                        # edges, including trivial paths. Never just zero the diagonal:
                        # every incoming/outgoing connection will be routed below.
                        if data.get('algorithm') != 'costmatrix':
                            raise
                        index = error.point_index
                        leg = self.leg(points[index],points[index],truck,policy,deadline)
                        if (leg.duration_seconds != 0 or leg.distance_m != 0
                                or any(p != leg.geometry[0] for p in leg.geometry)):
                            raise EngineError('Matrix self-arc disagreement was not confirmed by a zero-length road path.')
                        affected.add(index)
                        cells = data['sources_to_targets']
                        cells['durations'][index-source][index-target] = leg.duration_seconds
                        cells['distances'][index-source][index-target] = leg.distance_m/1000
                for row in range(source_count):
                    times[source+row][target:target+target_count] = block.durations[row]
                    distances[source+row][target:target+target_count] = block.distances[row]
                deadline.remaining()
        # Preserve the original forbidden-arc mask. Recalculate only finite arcs;
        # an HTTP/no-path/geometry/timeout failure rejects the entire calculation.
        # No failure is converted into a fabricated road or an unreachable cell.
        pairs = [(i, j) for i in range(n) for j in range(n)
                 if i != j and times[i][j] is not None and (i in affected or j in affected)]
        for (i, j), leg in zip(pairs, self.legs([(points[i], points[j], None, None) for i, j in pairs],
                                                truck, policy, deadline)):
            times[i][j], distances[i][j] = leg.duration_seconds, leg.distance_m
        deadline.remaining()
        if affected:
            logging.getLogger(__name__).warning('Matrix connections recalculated from road paths for %s affected points.',len(affected))
        if time_bounds is not None:
            inverse = [0]*n
            for internal, external in enumerate(order):
                inverse[external] = internal
            times = [[times[i][j] for j in inverse] for i in inverse]
            distances = [[distances[i][j] for j in inverse] for i in inverse]
        return Matrix(times,distances)

    def legs(self, requests: list[tuple], truck: Truck, policy: Policy, deadline: Deadline) -> list[Leg]:
        """Route (start, end, from_id, to_id) requests in order, bounded like matrix tiles.

        At most MATRIX_CONCURRENCY calls are in flight; each wave completes before
        the next starts. Any failure rejects every result: queued requests are
        cancelled and active calls joined. The failing request's position is
        recorded on the exception as ``request_index``.
        """
        def route(index):
            start, end, from_id, to_id = requests[index]
            deadline.remaining()
            try:
                return self.leg(start, end, truck, policy, deadline, from_id, to_id)
            except EngineError as error:
                error.request_index = index
                raise
        if self.matrix_concurrency == 1 or len(requests) <= 1:
            return [route(index) for index in range(len(requests))]
        results = []
        pool = ThreadPoolExecutor(max_workers=self.matrix_concurrency, thread_name_prefix='routing-leg')
        try:
            for start in range(0, len(requests), self.matrix_concurrency):
                wave = [pool.submit(route, index) for index in range(start, min(start+self.matrix_concurrency, len(requests)))]
                try:
                    results.extend(future.result(timeout=deadline.remaining()) for future in wave)
                except TimeoutError as error:
                    raise EngineError('Calculation deadline exceeded while waiting for a road path.') from error
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return results

    def leg(self, start: Location, end: Location, truck: Truck, policy: Policy, deadline: Deadline,
            from_id=None, to_id=None) -> Leg:
        data = self.post('route', {**self.base(truck, policy), 'shape_format': 'polyline6',
            'locations': [self.point(start), self.point(end)]}, deadline)
        try:
            trip = data['trip']
            if trip['status'] != 0 or trip['units'] != 'kilometers' or len(trip['legs']) != 1:
                raise ValueError()
            leg = trip['legs'][0]
            geometry = decode_shape(leg['shape'])
            if len(geometry) < 2 or any(len(p) != 2 or not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90 for p in geometry):
                raise ValueError()
            if meters(coordinates(start), geometry[0]) > self.snap_limit_m:
                raise SnapError('start', self.snap_limit_m)
            if meters(coordinates(end), geometry[-1]) > self.snap_limit_m:
                raise SnapError('end', self.snap_limit_m)
            return Leg(from_order_id=from_id, to_order_id=to_id,
                       duration_seconds=whole(leg['summary']['time']), distance_m=whole(leg['summary']['length'], 1000),
                       geometry=geometry)
        except (KeyError, TypeError, ValueError) as error:
            raise EngineError('Incomplete or invalid Valhalla road path.') from error
