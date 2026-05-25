from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .memorix.app_context import ScopeRuntimeManager
from .memorix.scope_router import ScopeRouter
from .memorix.services import AdminService, IngestService, MemoryService, ProfileService, QueryService
from .memorix.tools import build_memorix_tools
from .memorix.webui.plugin_page_bridge import PluginPageWebUIBridge


@register("astrbot_plugin_memorix", "Codex", "A_memorix memory plugin with embedded WebUI", "0.4.0")
class MemorixPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = dict(config or {})
        self.scope_router = ScopeRouter(mode=str(self.config.get("scope", {}).get("mode", "group_global")))
        self.runtime_manager = ScopeRuntimeManager(
            plugin_name="astrbot_plugin_memorix",
            plugin_config=self.config,
            astrbot_context=context,
        )

        self.ingest_service = IngestService(self.runtime_manager, self.config)
        self.query_service = QueryService(self.runtime_manager)
        self.memory_service = MemoryService(self.runtime_manager)
        self.profile_service = ProfileService(self.runtime_manager)
        self.admin_service = AdminService(self.runtime_manager)
        self.webui_page_bridge = PluginPageWebUIBridge(
            runtime_manager=self.runtime_manager,
            plugin_config=self.config,
            scope_resolver=self._resolve_dashboard_webui_scope,
        )

    async def initialize(self):
        logger.info("[memorix] initialize start")
        self.webui_page_bridge.register(self.context, plugin_name="astrbot_plugin_memorix")
        self._llm_tools = build_memorix_tools(self)
        self.context.add_llm_tools(*self._llm_tools)
        logger.info("[memorix] initialize done")

    async def terminate(self):
        logger.info("[memorix] terminate start")
        self._remove_llm_tools()
        await self.webui_page_bridge.close()
        await self.admin_service.close()
        await self.runtime_manager.close_all()
        logger.info("[memorix] terminate done")

    def _remove_llm_tools(self) -> None:
        tool_manager = getattr(self.context, "get_llm_tool_manager", lambda: None)()
        if tool_manager is None:
            return
        remove_func = getattr(tool_manager, "remove_func", None)
        if not callable(remove_func):
            return
        for tool in getattr(self, "_llm_tools", []) or []:
            remove_func(tool.name)

    def _resolve_scope(self, event: AstrMessageEvent) -> str:
        return self.scope_router.resolve(event)

    def _resolve_dashboard_webui_scope(self) -> str:
        configured = str(self.config.get("webui", {}).get("scope", "auto") or "auto").strip()
        mode = configured.lower()
        if mode not in {"", "auto", "current", "event"}:
            return configured
        known_scopes = self.runtime_manager.get_known_scopes()
        if known_scopes:
            return str(known_scopes[-1])
        return "default"
