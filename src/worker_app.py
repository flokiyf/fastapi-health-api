import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import asgi

from app import app

Shutdown = Callable[[], Awaitable[None]]

_start_lock = asyncio.Lock()
_shutdown: Shutdown | None = None


async def fetch(request: Any, env: Any):
    """Serve requests while keeping one FastMCP lifespan per Worker isolate.

    Cloudflare's public ``asgi.fetch`` starts and stops ASGI lifespan for every
    request. FastMCP's HTTP session manager is intentionally single-use, so its
    lifespan must instead remain active for the lifetime of the Worker isolate.
    """
    global _shutdown

    if _shutdown is None:
        async with _start_lock:
            if _shutdown is None:
                _shutdown = await asgi.start_application(app)

    return await asgi.process_request(app, request, env, None)
