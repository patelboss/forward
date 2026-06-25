"""
main.py
-------
Application entry point for the Telegram Forwarder system.

Boot sequence
─────────────
1.  Start the aiohttp health-check HTTP server (required by Koyeb).
2.  Connect to MongoDB Atlas and verify connectivity.
3.  Instantiate the Master Bot (Pyrogram Client in bot mode).
4.  Dynamically discover and load every module inside the plugins/ directory.
    Each plugin must expose a `register(app)` callable (sync or async).
5.  Start the Master Bot.
6.  The forwarder plugin's register() coroutine starts the userbot worker
    in the background if a valid session exists in MongoDB.
7.  Block until the event loop is interrupted (SIGINT / SIGTERM).
8.  Gracefully shutdown: stop worker → stop bot → close DB → stop health server.

Adding new plugins
──────────────────
Drop a .py file into the plugins/ directory and expose a `register(app)`
function.  No changes to this file are required.
"""

import asyncio
import importlib
import importlib.util
import inspect
import os
import sys
from pathlib import Path

from pyrogram import Client, idle

from config import cfg
from database import init_db, close_db
from utils.health import start_health_server, stop_health_server

# ── Directory that contains plugin modules ────────────────────────────────────
PLUGINS_DIR = Path(__file__).parent / "plugins"


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic plugin loader
# ─────────────────────────────────────────────────────────────────────────────

async def load_plugins(app: Client) -> None:
    """
    Iterates over every .py file in the plugins/ directory (excluding
    __init__.py and files starting with _), imports them as modules, and
    calls their register() function.

    register() may be:
        - a regular function:  register(app)  → called directly
        - a coroutine function: async def register(app) → awaited

    Any plugin that raises during registration logs the error and continues
    so a single bad plugin cannot prevent others from loading.
    """
    plugin_files = sorted(PLUGINS_DIR.glob("*.py"))

    for plugin_path in plugin_files:
        name = plugin_path.stem
        if name.startswith("_"):
            continue  # Skip __init__ and private files

        module_name = f"plugins.{name}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            if spec is None or spec.loader is None:
                print(f"[LOADER][WARN] Could not create spec for {plugin_path}", file=sys.stderr)
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[attr-defined]

        except Exception as exc:
            print(f"[LOADER][ERROR] Failed to import plugin '{name}': {exc}", file=sys.stderr)
            continue

        # Call register() if it exists
        register_fn = getattr(module, "register", None)
        if register_fn is None:
            print(f"[LOADER][WARN] Plugin '{name}' has no register() function – skipped.", flush=True)
            continue

        try:
            result = register_fn(app)
            # Support both sync and async register functions
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            print(f"[LOADER][ERROR] register() in plugin '{name}' raised: {exc}", file=sys.stderr)
            continue

        print(f"[LOADER] Plugin '{name}' loaded successfully.", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Graceful shutdown
# ─────────────────────────────────────────────────────────────────────────────

async def shutdown(bot: Client) -> None:
    """
    Ordered shutdown:
        1. Stop the userbot worker (if running)
        2. Stop the Master Bot
        3. Close MongoDB connection
        4. Stop the health-check HTTP server
    """
    print("\n[MAIN] Shutdown signal received. Stopping services …", flush=True)

    # Stop userbot worker via forwarder module if it was loaded
    try:
        forwarder_module = sys.modules.get("plugins.forwarder")
        if forwarder_module and hasattr(forwarder_module, "stop_worker"):
            await forwarder_module.stop_worker()
    except Exception as exc:
        print(f"[MAIN][WARN] Worker stop error: {exc}", file=sys.stderr)

    try:
        await bot.stop()
        print("[MAIN] Master Bot stopped.", flush=True)
    except Exception as exc:
        print(f"[MAIN][WARN] Bot stop error: {exc}", file=sys.stderr)

    await close_db()
    await stop_health_server()
    print("[MAIN] All services stopped. Goodbye.", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main async entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 60, flush=True)
    print("  Telegram Forwarder — booting …", flush=True)
    print("=" * 60, flush=True)

    # 1. Health-check HTTP server (must be up before Koyeb's grace period ends)
    await start_health_server()

    # 2. MongoDB
    await init_db()

    # 3. Master Bot client
    bot = Client(
        name="master_bot",
        api_id=cfg.API_ID,
        api_hash=cfg.API_HASH,
        bot_token=cfg.BOT_TOKEN,
    )

    # 4. Load all plugins (also starts the userbot worker if session exists)
    await load_plugins(bot)

    # 5. Start the Master Bot
    await bot.start()
    bot_info = await bot.get_me()
    print(
        f"[MAIN] Master Bot @{bot_info.username} is online.",
        flush=True,
    )

    print("[MAIN] System ready. Listening for events …", flush=True)

    # 6. Block until SIGINT / SIGTERM
    try:
        await idle()
    except (KeyboardInterrupt, SystemExit):
        pass

    # 7. Graceful shutdown
    await shutdown(bot)


# ─────────────────────────────────────────────────────────────────────────────
# Entry guard
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
