"""Independent exhaustive full-coverage reference for tiny static delivery cases.

No optimizer payload, solver summaries or production validator are used. Earliest
departure is sufficient for feasibility with static travel and hard start windows;
both supported objective scores exclude service/waiting. Integral rates avoid any
claim about native monetary rounding. Partial coverage/priorities are out of scope.
"""
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations_with_replacement, permutations
from uuid import UUID

from app.planning import PlanningInput
from app.routing import Matrix


@dataclass(frozen=True)
class Reference:
    score: int
    routes: tuple[tuple[int, ...], ...]


class TinyReference:
    def __init__(self, inputs: PlanningInput, matrices: list[Matrix]):
        if len(inputs.orders) > 6 or len(inputs.vehicles) > 3:
            raise ValueError('Exact reference is bounded to six orders and three vehicles.')
        if any(o.data.priority for o in inputs.orders):
            raise ValueError('Reference compares full coverage only; nonzero priority is unsupported.')
        self.inputs = inputs
        self.points = [inputs.depot.data.location] + [o.data.location for o in inputs.orders]
        if len({(p.longitude,p.latitude) for p in self.points}) != len(self.points):
            raise ValueError('Tiny reference requires distinct depot/order coordinates.')
        self.profiles = []
        for v in inputs.vehicles:
            if v.data.routing not in self.profiles:
                self.profiles.append(v.data.routing)
            if inputs.objective == 'MODELED_TRAVEL_COST' and (v.data.cost.per_hour_minor % 3600 or v.data.cost.per_km_minor % 1000):
                raise ValueError('Reference requires integral per-second and per-meter rates.')
        n = len(self.points)
        if len(matrices) != len(self.profiles):
            raise ValueError('Missing profile matrix.')
        for matrix in matrices:
            if len(matrix.durations) != n or len(matrix.distances) != n:
                raise ValueError('Matrix shape mismatch.')
            for times, distances in zip(matrix.durations, matrix.distances):
                if len(times) != n or len(distances) != n:
                    raise ValueError('Matrix row shape mismatch.')
                for t,d in zip(times,distances):
                    if (t is None) != (d is None) or any(x is not None and (type(x) is not int or x < 0) for x in (t,d)):
                        raise ValueError('Invalid matrix cell.')
        self.matrices = matrices

    def route_score(self, vehicle: int, sequence: tuple[int, ...], departure: int | None = None) -> int | None:
        if not sequence:
            return 0
        inputs = self.inputs
        v = inputs.vehicles[vehicle].data
        jobs = [inputs.orders[i].data for i in sequence]
        if any(not set(o.required_skills) <= set(v.skills) for o in jobs):
            return None
        # Explicit dimensions rather than using the adapter's vector conversion.
        for dimension in ('weight_kg','volume_dm3','pallet_eur_units','pallet_ind_units','loading_length_mm'):
            if sum(getattr(o.demand,dimension) for o in jobs) > getattr(v.capacity,dimension):
                return None
        if v.limits.max_stops is not None and len(sequence) > v.limits.max_stops:
            return None
        now = inputs.seconds(v.available_window.start) if departure is None else departure
        if now < inputs.seconds(v.available_window.start) or now > inputs.seconds(v.available_window.end):
            return None
        matrix = self.matrices[self.profiles.index(v.routing)]
        driving = distance = 0
        chain = (0, *(i+1 for i in sequence), 0)
        for stop, (a,b) in enumerate(zip(chain,chain[1:])):
            duration, meters = matrix.durations[a][b], matrix.distances[a][b]
            if duration is None:
                return None
            driving += duration; distance += meters; now += duration
            if b:
                order = jobs[stop]
                now = max(now, inputs.seconds(order.time_window.start))
                if now > inputs.seconds(order.time_window.end):
                    return None
                now += order.service_seconds
        if now > inputs.seconds(v.available_window.end):
            return None
        if v.limits.max_driving_seconds is not None and driving > v.limits.max_driving_seconds:
            return None
        if v.limits.max_distance_m is not None and distance > v.limits.max_distance_m:
            return None
        if inputs.objective == 'TRAVEL_TIME':
            return driving
        return v.cost.fixed_minor + driving*(v.cost.per_hour_minor//3600) + distance*(v.cost.per_km_minor//1000)

    def optimum(self) -> Reference | None:
        n, vehicles = len(self.inputs.orders), len(self.inputs.vehicles)
        best = None
        score_route = lru_cache(maxsize=None)(self.route_score)
        # Every order permutation and weak partition among the labeled vehicles.
        # Includes unused vehicles and every full-coverage assignment/sequence.
        for order in permutations(range(n)):
            for cuts in combinations_with_replacement(range(n+1),vehicles-1):
                boundaries = (0,*cuts,n)
                routes = tuple(order[a:b] for a,b in zip(boundaries,boundaries[1:]))
                scores = [score_route(i,route) for i,route in enumerate(routes)]
                if all(s is not None for s in scores):
                    score = sum(scores)
                    if best is None or score < best.score:
                        best = Reference(score,routes)
        return best

    def candidate_score(self, routes: list[tuple[UUID,int,list[UUID]]], unassigned: list[UUID]) -> int:
        vehicle_ids = [v.id for v in self.inputs.vehicles]
        order_ids = [o.id for o in self.inputs.orders]
        served = [key for _,_,ids in routes for key in ids]
        if unassigned or len(served) != len(set(served)) or set(served) != set(order_ids):
            raise ValueError('Candidate does not have equivalent full coverage.')
        if len({v for v,_,_ in routes}) != len(routes):
            raise ValueError('Candidate duplicates a vehicle.')
        scores = [self.route_score(vehicle_ids.index(v),tuple(order_ids.index(o) for o in ids),departure)
                  for v,departure,ids in routes]
        if any(score is None for score in scores):
            raise ValueError('Candidate violates a hard constraint.')
        return sum(scores)
