"""Streaming HTTP and WebSocket proxy for private code-server tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from fastapi import Request, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketState

WORKSPACE_SESSION_COOKIE = "aurinara_workspace_session"

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "authorization",
}
WEBSOCKET_ONLY_HEADERS = {
    "origin",
    "sec-websocket-accept",
    "sec-websocket-extensions",
    "sec-websocket-key",
    "sec-websocket-protocol",
    "sec-websocket-version",
}


def _strip_proxy_cookie(cookie_header: str) -> str:
    kept: list[str] = []
    for pair in cookie_header.split(";"):
        name, _, _ = pair.strip().partition("=")
        if name != WORKSPACE_SESSION_COOKIE and pair.strip():
            kept.append(pair.strip())
    return "; ".join(kept)


def _request_headers(
    headers: Iterable[tuple[str, str]], *, websocket: bool = False
) -> dict[str, str]:
    excluded = HOP_BY_HOP_HEADERS | (WEBSOCKET_ONLY_HEADERS if websocket else set())
    result: dict[str, str] = {}
    for key, value in headers:
        lowered = key.lower()
        if lowered in excluded:
            continue
        if lowered == "cookie":
            value = _strip_proxy_cookie(value)
            if not value:
                continue
        result[key] = value
    return result


def _response_headers(
    headers: Iterable[tuple[str, str]], target_base_url: str, public_base_path: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers:
        if key.lower() in HOP_BY_HOP_HEADERS | {"content-encoding", "set-cookie"}:
            continue
        if key.lower() == "location":
            if value.startswith(target_base_url.rstrip("/")):
                parsed = urlsplit(value)
                value = urlunsplit(
                    ("", "", f"{public_base_path.rstrip('/')}{parsed.path}", parsed.query, "")
                )
            elif value.startswith("/") and not value.startswith(public_base_path.rstrip("/")):
                value = f"{public_base_path.rstrip('/')}{value}"
        result[key] = value
    return result


async def proxy_http_request(
    request: Request, target_base_url: str, path: str, public_base_path: str
) -> StreamingResponse:
    target_url = f"{target_base_url.rstrip('/')}/{path.lstrip('/')}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    client = httpx.AsyncClient(follow_redirects=False, timeout=None)
    upstream = await client.send(
        client.build_request(
            request.method,
            target_url,
            headers=_request_headers(request.headers.items()),
            content=request.stream(),
        ),
        stream=True,
    )

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers.items(), target_base_url, public_base_path),
        background=BackgroundTask(close),
    )


def _websocket_url(target_base_url: str, path: str, query: str) -> str:
    base = target_base_url.rstrip("/")
    if base.startswith("https://"):
        base = f"wss://{base.removeprefix('https://')}"
    elif base.startswith("http://"):
        base = f"ws://{base.removeprefix('http://')}"
    url = f"{base}/{path.lstrip('/')}"
    return f"{url}?{query}" if query else url


async def proxy_websocket(websocket: WebSocket, target_base_url: str, path: str) -> None:
    protocol_header = websocket.headers.get("sec-websocket-protocol")
    subprotocols = (
        [value.strip() for value in protocol_header.split(",") if value.strip()]
        if protocol_header
        else None
    )
    try:
        async with websockets.connect(
            _websocket_url(target_base_url, path, websocket.url.query),
            additional_headers=_request_headers(websocket.headers.items(), websocket=True),
            subprotocols=subprotocols,
            proxy=None,
            max_size=None,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def client_to_upstream() -> None:
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            await upstream.close()
                            return
                        if message.get("text") is not None:
                            await upstream.send(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send(message["bytes"])
                except WebSocketDisconnect:
                    await upstream.close()

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)

            tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
            if websocket.application_state == WebSocketState.CONNECTED:
                await websocket.close()
    except Exception:
        if websocket.application_state == WebSocketState.CONNECTING:
            await websocket.accept()
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close(code=1011)
