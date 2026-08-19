"""FastAPI application: lifespan, webhook endpoint, polling mode, healthcheck.

Two operating modes:
  * Webhook (prod): MAX POSTs updates to /webhook/max; we deserialize with
    maxapi's process_update_webhook and dispatch via the Dispatcher.
  * Polling (dev):  dp.start_polling(bot) runs as a background task.

Selected by MAX_USE_POLLING in .env.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.context import init_context, get_context
from app.max.client import setup_bot
from app.webhook_runtime import WebhookTaskSupervisor

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("maxbot.main")

_settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx = await init_context(_settings)
    logger.info("App context initialised")

    bot, dp = await setup_bot()
    ctx.bot = bot
    app.state.bot = bot
    app.state.dp = dp
    app.state.webhook_runtime = WebhookTaskSupervisor()

    polling_task: asyncio.Task | None = None
    if _settings.max_use_polling:
        logger.info("Starting long polling")
        polling_task = asyncio.create_task(dp.start_polling(bot))
    else:
        logger.info("Webhook mode; waiting for updates at %s", _settings.webhook_path)
        # start_polling() normally runs __ready() to register handlers/bot.
        # In webhook mode we must init the dispatcher ourselves before dp.handle.
        await dp._Dispatcher__ready(bot)

    yield

    if polling_task is not None:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    if getattr(app.state, "webhook_runtime", None) is not None:
        await app.state.webhook_runtime.aclose()
    if getattr(ctx, "hermes", None) is not None:
        await ctx.hermes.aclose()
    await ctx.orchestrator.aclose()
    await ctx.llm.aclose()
    await ctx.storage.close()
    logger.info("Shutdown complete")


app = FastAPI(title="MAX AI Bot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(_settings.webhook_path)
async def webhook(request: Request) -> Response:
    """Acknowledge a MAX update immediately, then dispatch it once in background."""
    event_json = await request.json()

    async def process() -> None:
        try:
            from maxapi.methods.types.getted_updates import process_update_webhook

            event = await process_update_webhook(
                event_json=event_json,
                bot=app.state.bot,
            )
            await app.state.dp.handle(event)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to process webhook update")

    accepted = app.state.webhook_runtime.submit(event_json, process)
    return JSONResponse(
        content={"ok": True, "duplicate": not accepted},
        status_code=200,
    )


def main() -> None:  # pragma: no cover - entrypoint for non-docker runs
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=_settings.app_host,
        port=_settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
