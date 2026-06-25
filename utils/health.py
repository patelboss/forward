"""
utils/health.py
---------------
Lightweight async HTTP server that satisfies Koyeb's platform health-check
requirements.

Koyeb monitors the $PORT assigned to the container.  If nothing responds with
HTTP 200 within the grace period the deployment is marked unhealthy and the
container is restarted endlessly.

This module starts an aiohttp.web server on 0.0.0.0:$PORT in the background
asyncio task.  It answers GET / and GET /health with a 200 OK JSON payload.

Usage (from main.py):
    from utils.health import start_health_server
    await start_health_server()
"""

import asyncio
import sys
from typing import Optional

from aiohttp import web

from config import cfg

# Module-level runner reference so we can cleanly shut it down if needed
_runner: Optional[web.AppRunner] = None


async def _health_handler(request: web.Request) -> web.Response:
    """Respond to any GET request with a 200 OK JSON body."""
    return web.json_response(
        {"status": "ok", "service": "tg-forwarder"},
        status=200,
    )


async def start_health_server() -> None:
    """
    Initialise and start the aiohttp application as a background asyncio task.
    Binds to 0.0.0.0:cfg.PORT.

    This function returns immediately after the server is listening; the server
    itself keeps running inside the event loop alongside Pyrogram clients.
    """
    global _runner

    app = web.Application()

    # Register the handler for both common health-check paths
    app.router.add_get("/", _health_handler)
    app.router.add_get("/health", _health_handler)

    _runner = web.AppRunner(app)
    await _runner.setup()

    site = web.TCPSite(_runner, host="0.0.0.0", port=cfg.PORT)

    try:
        await site.start()
        print(
            f"[HEALTH] HTTP health-check server listening on 0.0.0.0:{cfg.PORT}",
            flush=True,
        )
    except OSError as exc:
        # Port already in use or permission denied – log and continue.
        # The app should not crash just because the health endpoint fails.
        print(
            f"[HEALTH][WARN] Could not bind to port {cfg.PORT}: {exc}",
            file=sys.stderr,
        )


async def stop_health_server() -> None:
    """Gracefully tear down the HTTP server during application shutdown."""
    global _runner
    if _runner:
        await _runner.cleanup()
        print("[HEALTH] HTTP health-check server stopped.", flush=True)
