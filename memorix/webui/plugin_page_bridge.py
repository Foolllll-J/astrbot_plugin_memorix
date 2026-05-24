"""AstrBot Plugin Pages bridge for the Memorix WebUI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict
from urllib.parse import urlsplit, urlunsplit

import httpx
from astrbot.api import logger

from ..amemorix.import_write_guard import ImportWriteGuardMiddleware
from ..amemorix.routers.v1_router import router as v1_router
from ..amemorix.services.import_task_manager import ImportTaskManager
from ..app_context import ScopeRuntimeManager
from .routes_compat import MemorixServer
from .server import _WebV1TaskManager

PLUGIN_PAGE_API_ROUTE = "webui/request"


@dataclass(slots=True)
class _EmbeddedWebUIApp:
    scope_key: str
    server: MemorixServer
    client: httpx.AsyncClient
    task_manager: _WebV1TaskManager
    import_task_manager: ImportTaskManager


class PluginPageWebUIBridge:
    """Expose the standalone FastAPI WebUI through AstrBot Plugin Pages.

    AstrBot Plugin Pages only proxy GET/POST requests to plugin Web APIs. The
    current A_memorix console still uses PUT/PATCH/DELETE, so the page sends a
    single authenticated POST tunnel here and this bridge dispatches the original
    request to an in-process FastAPI app for the selected Memorix scope.
    """

    def __init__(
        self,
        *,
        runtime_manager: ScopeRuntimeManager,
        plugin_config: Dict[str, Any],
        scope_resolver: Callable[[], str],
    ) -> None:
        self.runtime_manager = runtime_manager
        self.plugin_config = plugin_config or {}
        self.scope_resolver = scope_resolver
        self._apps: Dict[str, _EmbeddedWebUIApp] = {}
        self._lock = asyncio.Lock()

    def register(self, context: Any, *, plugin_name: str) -> None:
        if not self._is_enabled():
            logger.info("[memorix] embedded WebUI disabled by config")
            return

        register_web_api = getattr(context, "register_web_api", None)
        if not callable(register_web_api):
            logger.warning("[memorix] AstrBot context does not support plugin Web APIs; embedded WebUI disabled")
            return

        register_web_api(
            f"/{plugin_name}/{PLUGIN_PAGE_API_ROUTE}",
            self.handle_request,
            ["POST"],
            "Memorix embedded WebUI request bridge",
        )

    async def close(self) -> None:
        apps = list(self._apps.values())
        self._apps.clear()
        for app in apps:
            try:
                await app.client.aclose()
            except Exception as exc:
                logger.warning("[memorix] embedded WebUI client close failed: %s", exc)
            try:
                await app.import_task_manager.stop()
            except Exception as exc:
                logger.warning("[memorix] embedded WebUI import task manager stop failed: %s", exc)
            try:
                await app.task_manager.stop()
            except Exception as exc:
                logger.warning("[memorix] embedded WebUI task manager stop failed: %s", exc)

    async def handle_request(self):
        from quart import jsonify, request

        try:
            payload = await request.get_json(force=True, silent=True)
            if not isinstance(payload, dict):
                raise ValueError("request payload must be a JSON object")

            method = str(payload.get("method") or "GET").upper()
            url = str(payload.get("url") or "").strip()
            body = payload.get("data")
            result = await self.dispatch(method=method, url=url, body=body)
            return jsonify({"status": "ok", "data": result})
        except Exception as exc:
            logger.warning("[memorix] embedded WebUI request failed: %s", exc, exc_info=True)
            return jsonify({"status": "error", "message": str(exc)})

    async def dispatch(self, *, method: str, url: str, body: Any = None) -> Any:
        if not self._is_enabled():
            raise RuntimeError("embedded WebUI is disabled by config")

        method = self._normalize_method(method)
        target = self._normalize_url(url)
        scope_key = self._resolve_scope_key()
        app = await self._get_app(scope_key)

        request_kwargs: Dict[str, Any] = {}
        if method != "GET" and body is not None:
            request_kwargs["json"] = body

        response = await app.client.request(method, target, **request_kwargs)
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            message = response.text or response.reason_phrase or f"HTTP {response.status_code}"
            if "application/json" in content_type:
                try:
                    payload = response.json()
                    message = str(payload.get("detail") or payload.get("message") or payload)
                except Exception:
                    pass
            raise RuntimeError(message)

        if not response.content:
            return None
        if "application/json" in content_type:
            return response.json()
        return response.text

    def _resolve_scope_key(self) -> str:
        raw = str(self.scope_resolver() or "").strip()
        return raw or "default"

    def _is_enabled(self) -> bool:
        webui_config = self.plugin_config.get("webui", {})
        if not isinstance(webui_config, dict):
            return True
        return bool(webui_config.get("enabled", True))

    @staticmethod
    def _normalize_method(method: str) -> str:
        normalized = str(method or "GET").strip().upper()
        if normalized not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"unsupported WebUI method: {normalized}")
        return normalized

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url:
            raise ValueError("missing WebUI request URL")
        parts = urlsplit(url)
        if parts.scheme or parts.netloc:
            raise ValueError("absolute WebUI request URL is not allowed")
        path = parts.path or "/"
        if any(segment == ".." for segment in path.split("/")):
            raise ValueError(f"unsupported WebUI request path: {path}")
        if not path.startswith(("/api/", "/v1/", "/healthz", "/readyz")):
            raise ValueError(f"unsupported WebUI request path: {path}")
        if path.startswith(("/api/plug/", "/api/plugin/")):
            raise ValueError(f"unsupported WebUI request path: {path}")
        return urlunsplit(("", "", path, parts.query, ""))

    async def _get_app(self, scope_key: str) -> _EmbeddedWebUIApp:
        cached = self._apps.get(scope_key)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._apps.get(scope_key)
            if cached is not None:
                return cached
            app = await self._create_app(scope_key)
            self._apps[scope_key] = app
            return app

    async def _create_app(self, scope_key: str) -> _EmbeddedWebUIApp:
        runtime = await self.runtime_manager.get_runtime(scope_key)
        server = MemorixServer(plugin_instance=runtime.context, host="127.0.0.1", port=0)
        app = server.app
        app.state.context = runtime.context
        if not bool(getattr(app.state, "_memorix_v1_router_registered", False)):
            app.include_router(v1_router)
            app.state._memorix_v1_router_registered = True
        if not bool(getattr(app.state, "_memorix_import_guard_registered", False)):
            app.add_middleware(ImportWriteGuardMiddleware)
            app.state._memorix_import_guard_registered = True

        task_manager = _WebV1TaskManager(runtime.context)
        await task_manager.start()
        app.state.task_manager = task_manager

        import_task_manager = ImportTaskManager(runtime.context)
        await import_task_manager.start()
        app.state.import_task_manager = import_task_manager

        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://memorix.local")
        logger.info("[memorix] embedded WebUI app ready: scope=%s", scope_key)
        return _EmbeddedWebUIApp(
            scope_key=scope_key,
            server=server,
            client=client,
            task_manager=task_manager,
            import_task_manager=import_task_manager,
        )
