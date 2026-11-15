"""Bounded request diagnostics without URLs, credentials, bodies or error messages.

An unexpected error is logged with where it happened (file, line and function of each frame),
never with its message, arguments or SQL, which can hold passwords or customer records.
"""
import logging
from pathlib import Path
import time
import traceback
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send, Message

logger = logging.getLogger(__name__)
APPLICATION = str(Path(__file__).resolve().parent)


def trace(error: BaseException, limit: int = 12) -> str:
    """Where `error` was raised: the application's own frames, outermost first, then the frame that
    raised it when that is in a library. File names and line numbers only, never values."""
    frames = traceback.extract_tb(error.__traceback__)
    own = [f for f in frames if str(Path(f.filename).resolve()).startswith(APPLICATION)][-limit:]
    if frames and (not own or own[-1] is not frames[-1]):
        own.append(frames[-1])
    return ' > '.join(f'{Path(f.filename).parent.name}/{Path(f.filename).name}:{f.lineno} {f.name}' for f in own) or 'none'


class RequestLog:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        started = time.monotonic()
        status = 500
        response_started = False
        error_type = 'none'

        async def observed(message: Message) -> None:
            nonlocal status, response_started
            if message['type'] == 'http.response.start':
                status = message['status']
                response_started = True
                headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'x-request-id']
                message = {**message, 'headers': [*headers, (b'x-request-id', request_id.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, observed)
        except Exception as error:
            # Never let Uvicorn print a traceback containing SQL or private values; say where instead.
            error_type = type(error).__name__
            logger.error('request_failed request_id=%s error_type=%s trace=%s', request_id, error_type, trace(error))
            if not response_started:
                await JSONResponse({'detail': 'Unexpected server error.', 'request_id': request_id},
                                   status_code=500, headers={'Cache-Control': 'no-store'})(scope, receive, observed)
            else:
                # Abort an incomplete stream; no second response or fabricated success.
                raise RuntimeError('Response stream interrupted; see safe request log.') from None
        finally:
            # Route templates come from server registration, never raw path/query values.
            route = getattr(scope.get('route'), 'path', 'unmatched')
            method = scope.get('method')
            if method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}:
                method = 'OTHER'
            logger.info('request request_id=%s method=%s route=%s status=%d elapsed=%.3f error_type=%s',
                        request_id, method, route, status, time.monotonic()-started, error_type)
