from __future__ import annotations

import re
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .memorix.adapters.astrbot_event_adapter import AstrbotEventAdapter
from .memorix.app_context import ScopeRuntimeManager
from .memorix.scope_router import ScopeRouter
from .memorix.services import (
    AdminService,
    IngestService,
    MemoryService,
    PersonFactWritebackItem,
    PersonFactWritebackService,
    ProfileService,
    QueryService,
    SummaryService,
)
from .memorix.tools import build_memorix_tools
from .memorix.utils.message_formatting import (
    format_astrbot_event_message,
    message_format_options_from_config,
)
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
        self.summary_service = SummaryService(self.runtime_manager)
        self.person_fact_writeback_service = PersonFactWritebackService(self.runtime_manager, self.config)
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
        await self.person_fact_writeback_service.start()
        logger.info("[memorix] initialize done")

    async def terminate(self):
        logger.info("[memorix] terminate start")
        self._remove_llm_tools()
        await self.person_fact_writeback_service.close()
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

    @staticmethod
    def _bool_cfg(config: dict, key: str, default: bool) -> bool:
        current = config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return bool(current)

    @staticmethod
    def _normalize_command_prefixes(raw) -> list[str]:
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple, set)):
            values = [str(item or "") for item in raw]
        else:
            values = []
        prefixes: list[str] = []
        seen = set()
        for item in values:
            prefix = str(item or "").strip()
            if not prefix or prefix in seen:
                continue
            seen.add(prefix)
            prefixes.append(prefix)
        return prefixes or ["/"]

    @staticmethod
    def _strip_leading_mentions(text: str) -> str:
        current = str(text or "").lstrip()
        while True:
            changed = False
            if current.startswith("@"):
                parts = current.split(maxsplit=1)
                if len(parts) == 2:
                    current = parts[1].lstrip()
                    changed = True
            elif current.startswith("[CQ:at,"):
                idx = current.find("]")
                if idx > 0:
                    current = current[idx + 1 :].lstrip()
                    changed = True
            if not changed:
                return current

    @staticmethod
    def _strip_leading_bot_mention(text: str, self_id: str) -> str:
        bot_id = str(self_id or "").strip()
        current = str(text or "").lstrip()
        if not bot_id:
            return current
        patterns = (
            rf"^@\S+\({re.escape(bot_id)}\)(?:\s+|$)",
            rf"^@{re.escape(bot_id)}(?:\s+|$)",
            rf"^\[CQ:at,qq={re.escape(bot_id)}\]\s*",
        )
        while True:
            updated = current
            for pattern in patterns:
                updated = re.sub(pattern, "", updated, count=1).lstrip()
            if updated == current:
                return current
            current = updated

    def _is_command_message(self, text: str) -> bool:
        ingest_cfg = self.config.get("ingest", {}) if isinstance(self.config.get("ingest"), dict) else {}
        prefixes = self._normalize_command_prefixes(
            ingest_cfg.get("command_prefixes", ingest_cfg.get("command_prefix", ["/"]))
        )
        content = str(text or "").lstrip()
        if not content:
            return False
        candidates = [content]
        mention_stripped = self._strip_leading_mentions(content)
        if mention_stripped and mention_stripped != content:
            candidates.append(mention_stripped)
        for candidate in candidates:
            for prefix in prefixes:
                if not candidate.startswith(prefix):
                    continue
                if len(candidate) == len(prefix):
                    return True
                if prefix[-1].isalnum():
                    next_char = candidate[len(prefix) : len(prefix) + 1]
                    if next_char and (next_char.isalnum() or next_char == "_"):
                        continue
                return True
        return False

    @staticmethod
    def _event_ctx_text(event: AstrMessageEvent, scope_key: str = "") -> str:
        scope = str(scope_key or "unknown")
        platform = str(getattr(event, "get_platform_name", lambda: "unknown")() or "unknown")
        sender = str(getattr(event, "get_sender_id", lambda: "")() or "")
        group = str(getattr(event, "get_group_id", lambda: "")() or "")
        session = str(getattr(getattr(event, "message_obj", None), "session_id", "") or getattr(event, "unified_msg_origin", ""))
        return f"scope={scope} platform={platform} session={session or '-'} sender={sender or '-'} group={group or '-'}"

    async def _format_event_text_for_memory(self, event: AstrMessageEvent) -> str:
        formatted = await format_astrbot_event_message(
            event,
            context=self.context,
            options=message_format_options_from_config(self.config),
        )
        self_id = str(
            getattr(event, "get_self_id", lambda: "")()
            or getattr(getattr(event, "message_obj", None), "self_id", "")
            or ""
        )
        return self._strip_leading_bot_mention(formatted.text, self_id)

    async def _ingest_event_message(self, event: AstrMessageEvent, role: str, text: str) -> None:
        adapted = AstrbotEventAdapter.from_event(event, self._resolve_scope(event))
        normalized_role = str(role or "user").strip().lower() or "user"
        self_id = str(
            getattr(event, "get_self_id", lambda: "")()
            or getattr(getattr(event, "message_obj", None), "self_id", "")
            or ""
        )
        sender_id = adapted.sender_id
        sender_name = adapted.sender_name
        event_timestamp = adapted.timestamp
        if normalized_role == "assistant":
            sender_id = self_id or "assistant"
            sender_name = "assistant"
            event_timestamp = time.time()
        elif normalized_role == "user" and adapted.sender_id and self_id and adapted.sender_id == self_id:
            return

        content = str(text or "").strip()
        if not content and self._bool_cfg(self.config, "ingest.skip_empty_text", True):
            return

        source = f"chat:{adapted.platform}:{adapted.session_id}"
        if normalized_role == "user":
            await self.profile_service.upsert_registry_from_event(
                scope_key=adapted.scope_key,
                platform=adapted.platform,
                sender_id=sender_id,
                sender_name=sender_name or sender_id,
                group_id=adapted.group_id,
                session_id=adapted.session_id,
                unified_msg_origin=adapted.unified_msg_origin,
                timestamp=float(event_timestamp) if event_timestamp else None,
            )
        await self.ingest_service.ingest_message(
            scope_key=adapted.scope_key,
            session_id=adapted.session_id,
            role=normalized_role,
            content=content,
            source=source,
            user_id=sender_id,
            group_id=adapted.group_id,
            platform=adapted.platform,
            unified_msg_origin=adapted.unified_msg_origin,
            sender_name=sender_name,
            message_id=adapted.message_id,
            role_origin=normalized_role,
            timestamp=event_timestamp,
            time_meta={"event_time": event_timestamp} if event_timestamp else None,
        )
        logger.debug(
            "[memorix] ingested role=%s chars=%s %s",
            normalized_role,
            len(content),
            self._event_ctx_text(event, adapted.scope_key),
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_all_messages(self, event: AstrMessageEvent):
        if not self._bool_cfg(self.config, "ingest.record_all_events", True):
            return
        try:
            text = await self._format_event_text_for_memory(event)
            if not text and self._bool_cfg(self.config, "ingest.skip_empty_text", True):
                logger.debug("[memorix] skip empty/placeholder message %s", self._event_ctx_text(event))
                return
            if self._bool_cfg(self.config, "ingest.skip_command_messages", True) and self._is_command_message(text):
                logger.debug("[memorix] skip command message %s", self._event_ctx_text(event))
                return
            await self._ingest_event_message(event, "user", text)
            if not self._bool_cfg(self.config, "summarization.auto_import.after_reply_only", True):
                adapted = AstrbotEventAdapter.from_event(event, self._resolve_scope(event))
                await self.summary_service.maybe_enqueue_auto_summary(
                    scope_key=adapted.scope_key,
                    session_id=adapted.session_id,
                )
        except Exception as exc:
            logger.warning("[memorix] ingest user message failed: %s (%s)", exc, self._event_ctx_text(event), exc_info=True)

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        text = str(getattr(resp, "completion_text", "") or "").strip()
        if not text:
            return
        adapted = AstrbotEventAdapter.from_event(event, self._resolve_scope(event))
        try:
            user_text = await self._format_event_text_for_memory(event)
            await self._ingest_event_message(event, "assistant", text)
            if user_text and not self._is_command_message(user_text):
                await self.person_fact_writeback_service.enqueue(
                    PersonFactWritebackItem(
                        scope_key=adapted.scope_key,
                        session_id=adapted.session_id,
                        user_text=user_text,
                        assistant_text=text,
                        user_id=adapted.sender_id,
                        group_id=adapted.group_id,
                        platform=adapted.platform,
                        sender_name=adapted.sender_name,
                        message_id=adapted.message_id,
                        timestamp=float(adapted.timestamp) if adapted.timestamp else time.time(),
                    )
                )
            result = await self.summary_service.maybe_enqueue_auto_summary(
                scope_key=adapted.scope_key,
                session_id=adapted.session_id,
            )
            if result.get("queued"):
                logger.debug(
                    "[memorix] auto summary queued task=%s %s",
                    str(result.get("task_id", "") or ""),
                    self._event_ctx_text(event, adapted.scope_key),
                )
        except Exception as exc:
            logger.warning("[memorix] ingest llm response failed: %s (%s)", exc, self._event_ctx_text(event), exc_info=True)
