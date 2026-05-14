from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from loguru import logger

from web.services.agent_service import session_store


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


def json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message, "detail": message}, status=status)


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(FRONTEND_DIR / "sales-agent.html")


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def chat(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return json_error("请求体必须是 JSON。")

    message = str(payload.get("message") or "").strip()
    if not message:
        return json_error("message 不能为空。")

    session_id = str(payload.get("session_id") or uuid4()).strip()

    try:
        result = await session_store.chat(session_id=session_id, message=message)
    except Exception as exc:
        logger.exception("Agent chat failed")
        return json_error(f"Agent 执行失败：{exc}", status=500)

    return web.json_response(result)


async def reset(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    session_id = str(payload.get("session_id") or uuid4()).strip()

    try:
        result = await session_store.reset(session_id=session_id)
    except Exception as exc:
        logger.exception("Agent reset failed")
        return json_error(f"会话清空失败：{exc}", status=500)

    return web.json_response(result)


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/reset", reset)
    app.router.add_options("/api/chat", lambda _: web.Response(status=204))
    app.router.add_options("/api/reset", lambda _: web.Response(status=204))
    app.router.add_static("/css", FRONTEND_DIR / "css", name="css")
    app.router.add_static("/js", FRONTEND_DIR / "js", name="js")
    app.router.add_static("/frontend", FRONTEND_DIR, name="frontend")
    return app


def main() -> None:
    host = os.getenv("SALES_AGENT_HOST", "127.0.0.1")
    port = int(os.getenv("SALES_AGENT_PORT", "8000"))
    logger.info("Starting sales agent web app at http://{}:{}", host, port)
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
