from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from loguru import logger


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from web.services.agent_service import session_store


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    if request.path == "/" or request.path.startswith(("/js/", "/css/", "/frontend/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
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


async def action(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return json_error("请求体必须是 JSON。")

    action_name = str(payload.get("action") or "").strip()
    if not action_name:
        return json_error("action 不能为空。")

    session_id = str(payload.get("session_id") or uuid4()).strip()
    case_id = payload.get("case_id")
    case_id = str(case_id).strip() if case_id else None

    try:
        result = await session_store.action(
            session_id=session_id,
            action=action_name,
            case_id=case_id,
        )
    except Exception as exc:
        logger.exception("Agent action failed")
        return json_error(f"Agent 操作失败：{exc}", status=500)

    return web.json_response(result)


async def new_case(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    session_id = str(payload.get("session_id") or uuid4()).strip()
    product_module = payload.get("product_module")
    product_module = str(product_module).strip() if product_module else None

    try:
        result = await session_store.new_case(session_id=session_id, product_module=product_module)
    except Exception as exc:
        logger.exception("Create case failed")
        return json_error(f"新建业务失败：{exc}", status=500)

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
    app.router.add_post("/api/action", action)
    app.router.add_post("/api/case/new", new_case)
    app.router.add_post("/api/reset", reset)
    app.router.add_options("/api/chat", lambda _: web.Response(status=204))
    app.router.add_options("/api/action", lambda _: web.Response(status=204))
    app.router.add_options("/api/case/new", lambda _: web.Response(status=204))
    app.router.add_options("/api/reset", lambda _: web.Response(status=204))
    app.router.add_static("/css", FRONTEND_DIR / "css", name="css")
    app.router.add_static("/js", FRONTEND_DIR / "js", name="js")
    app.router.add_static("/frontend", FRONTEND_DIR, name="frontend")
    return app


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def resolve_port(host: str, preferred_port: int) -> int:
    if os.getenv("SALES_AGENT_PORT"):
        return preferred_port

    port = preferred_port
    while not port_is_available(host, port):
        port += 1
    return port


def main() -> None:
    host = os.getenv("SALES_AGENT_HOST", "127.0.0.1")
    port = resolve_port(host, int(os.getenv("SALES_AGENT_PORT", "8000")))
    logger.info("Starting sales agent web app at http://{}:{}", host, port)
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()

# 命令行运行 python -m web.app
