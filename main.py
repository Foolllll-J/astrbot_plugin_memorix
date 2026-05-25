from __future__ import annotations

import shlex
import time
from typing import Any, Dict, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .memorix.adapters.astrbot_event_adapter import AstrbotEventAdapter
from .memorix.app_context import ScopeRuntimeManager
from .memorix.commands.mem_commands import to_pretty_text
from .memorix.scope_router import ScopeRouter
from .memorix.services import IngestService, MemoryService, ProfileService, QueryService, SummaryService
from .memorix.tasks.maintenance_scheduler import MaintenanceScheduler
from .memorix.tools import build_memorix_tools
from .memorix.webui.plugin_page_bridge import PluginPageWebUIBridge


@register("astrbot_plugin_memorix", "Codex", "A_memorix memory plugin with embedded WebUI", "0.4.0")
class MemorixPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self._config_obj = config if hasattr(config, "save_config") else None
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
        self.maintenance_scheduler = MaintenanceScheduler(runtime_manager=self.runtime_manager)
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
    def _parse_tail(raw_text: str, sub_cmd: str) -> str:
        text = str(raw_text or "").strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        for prefix in ("mem", "astrbot_plugin_memorix"):
            matched, text = MemorixPlugin._consume_head_token(text, prefix)
            if matched:
                break
        matched, text = MemorixPlugin._consume_head_token(text, sub_cmd)
        if matched:
            return text
        return text

    @staticmethod
    def _consume_head_token(text: str, token: str) -> tuple[bool, str]:
        raw = str(text or "").strip()
        name = str(token or "").strip()
        if not raw or not name:
            return False, raw
        if raw == name:
            return True, ""
        if not raw.startswith(name):
            return False, raw
        next_char = raw[len(name) : len(name) + 1]
        if next_char and not next_char.isspace():
            return False, raw
        return True, raw[len(name) :].strip()

    @classmethod
    def _parse_tail_tokens(cls, raw_text: str, sub_cmd: str) -> list[str]:
        tail = cls._parse_tail(raw_text, sub_cmd)
        if not tail:
            return []
        try:
            return [str(token).strip() for token in shlex.split(tail) if str(token).strip()]
        except ValueError:
            return [token for token in tail.split() if token]

    @classmethod
    def _parse_direct_command_tokens(cls, raw_text: str, command: str) -> list[str]:
        text = str(raw_text or "").strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        matched, tail = cls._consume_head_token(text, command)
        if not matched:
            return []
        if not tail:
            return []
        try:
            return [str(token).strip() for token in shlex.split(tail) if str(token).strip()]
        except ValueError:
            return [token for token in tail.split() if token]

    @staticmethod
    def _to_int(raw: Any, default: int, min_value: int = 1) -> int:
        try:
            return max(int(raw), min_value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(raw: Any, default: float, min_value: float = 0.0) -> float:
        try:
            return max(float(raw), min_value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _event_ctx_text(event: AstrMessageEvent, scope_key: Optional[str] = None) -> str:
        scope = str(scope_key or "").strip() or "unknown"
        platform = str(getattr(event, "get_platform_name", lambda: "unknown")() or "unknown")
        sender = str(getattr(event, "get_sender_id", lambda: "")() or "")
        group = str(getattr(event, "get_group_id", lambda: "")() or "")
        message_obj = getattr(event, "message_obj", None)
        session = str(getattr(message_obj, "session_id", "") or getattr(event, "unified_msg_origin", ""))
        return (
            f"scope={scope} platform={platform} "
            f"session={session or '-'} sender={sender or '-'} group={group or '-'}"
        )

    def _log_cmd(self, event: AstrMessageEvent, command: str, **fields: Any) -> None:
        scope_key = self._resolve_scope(event)
        ctx = self._event_ctx_text(event, scope_key)
        detail = " ".join(f"{key}={value}" for key, value in fields.items())
        if detail:
            logger.info("[memorix] cmd=%s %s %s", command, ctx, detail)
        else:
            logger.info("[memorix] cmd=%s %s", command, ctx)

    @staticmethod
    def _person_profile_global_usage() -> str:
        return "用法: /person_profile_global on|off|status 或 /mem profile_global on|off|status"

    @staticmethod
    def _person_profile_global_mode_enabled(policy: Dict[str, Any]) -> bool:
        return bool(policy.get("enabled", True) and policy.get("global_injection_enabled", False))

    def _sync_local_person_profile_policy(
        self,
        *,
        global_injection_enabled: Optional[bool] = None,
    ) -> None:
        person_cfg = self.config.get("person_profile")
        if not isinstance(person_cfg, dict):
            person_cfg = {}
            self.config["person_profile"] = person_cfg
        if global_injection_enabled is not None:
            person_cfg["global_injection_enabled"] = bool(global_injection_enabled)

    def _persist_plugin_config(self) -> tuple[bool, str]:
        cfg_obj = self._config_obj
        if cfg_obj is None:
            return False, "当前运行环境不支持插件配置落盘"
        try:
            cfg_obj.save_config(replace_config=dict(self.config))
            return True, "配置已持久化到插件配置文件"
        except Exception as exc:
            logger.error("[memorix] persist plugin config failed: %s", exc, exc_info=True)
            return False, f"配置落盘失败: {exc}"

    async def _handle_person_profile_global_action(self, action: str) -> Dict[str, Any]:
        act = str(action or "").strip().lower() or "status"
        if act not in {"on", "off", "status"}:
            raise ValueError(self._person_profile_global_usage())

        if act == "on":
            self._sync_local_person_profile_policy(
                global_injection_enabled=True,
            )
            policy = await self.runtime_manager.apply_person_profile_policy(
                global_injection_enabled=True,
            )
            policy["message"] = "已开启全局人物画像：将强制注入所有会话画像（仍受 person_profile.enabled 总开关约束）。"
            persisted, persist_message = self._persist_plugin_config()
            policy["persisted"] = persisted
            policy["persist_message"] = persist_message
        elif act == "off":
            self._sync_local_person_profile_policy(
                global_injection_enabled=False,
            )
            policy = await self.runtime_manager.apply_person_profile_policy(
                global_injection_enabled=False,
            )
            policy["message"] = "已关闭全局人物画像：恢复为 opt_in/default 的常规策略。"
            persisted, persist_message = self._persist_plugin_config()
            policy["persisted"] = persisted
            policy["persist_message"] = persist_message
        else:
            policy = self.runtime_manager.get_person_profile_policy()
            policy["message"] = "当前为人物画像全局策略状态。"
            policy["persisted"] = None
            policy["persist_message"] = "status 查询不会修改配置文件"

        policy["global_mode_enabled"] = self._person_profile_global_mode_enabled(policy)
        return policy

    @filter.command("person_profile")
    async def person_profile_switch(self, event: AstrMessageEvent):
        """开关当前对话的人物画像注入（on/off/status）。"""
        tokens = self._parse_direct_command_tokens(event.message_str, "person_profile")
        action = str(tokens[0]).strip().lower() if tokens else "status"
        if action not in {"on", "off", "status"}:
            yield event.plain_result("用法: /person_profile on|off|status")
            return

        scope_key = self._resolve_scope(event)
        adapted = AstrbotEventAdapter.from_event(event, scope_key)
        session_id = str(adapted.session_id or "").strip()
        user_id = str(adapted.sender_id or "").strip()
        if not session_id or not user_id:
            yield event.plain_result("无法识别当前会话范围（session_id/user_id）")
            return

        try:
            if action == "status":
                data = await self.profile_service.get_injection_status(
                    scope_key=scope_key,
                    session_id=session_id,
                    user_id=user_id,
                )
            else:
                data = await self.profile_service.set_injection_switch(
                    scope_key=scope_key,
                    session_id=session_id,
                    user_id=user_id,
                    enabled=(action == "on"),
                )
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] person_profile command failed: %s", exc, exc_info=True)
            yield event.plain_result(f"人物画像开关操作失败: {exc}")

    @filter.command("person_profile_global")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def person_profile_global_switch(self, event: AstrMessageEvent):
        """统一设置所有对话的人物画像注入开关。"""
        tokens = self._parse_direct_command_tokens(event.message_str, "person_profile_global")
        action = str(tokens[0]).strip().lower() if tokens else "status"
        self._log_cmd(event, "person_profile_global", action=action)
        try:
            data = await self._handle_person_profile_global_action(action)
            yield event.plain_result(to_pretty_text(data))
        except ValueError as exc:
            yield event.plain_result(str(exc))
        except Exception as exc:
            logger.error("[memorix] person_profile_global command failed: %s", exc, exc_info=True)
            yield event.plain_result(f"全局人物画像开关操作失败: {exc}")

    @filter.command_group("mem")
    def mem(self):
        """记忆系统：查询、管理和维护记忆数据。"""
        pass

    @mem.command("status")
    async def mem_status(self, event: AstrMessageEvent):
        """查看当前作用域的记忆状态和服务信息。"""
        self._log_cmd(event, "status")
        scope_key = self._resolve_scope(event)
        data = await self.memory_service.status(scope_key=scope_key)
        scheduler = await self.maintenance_scheduler.status(scope_key=scope_key)
        payload = {
            "scope": scope_key,
            "known_scopes": self.runtime_manager.get_known_scopes(),
            "webui": {
                "embedded": True,
                "scope": self._resolve_dashboard_webui_scope(),
                "page": "Dashboard -> 插件详情 -> Memorix 控制台",
            },
            "scheduler": scheduler,
            "memory": data,
        }
        yield event.plain_result(to_pretty_text(payload))

    @mem.command("query")
    async def mem_query(self, event: AstrMessageEvent, query: str = "", top_k: int = 10):
        """语义搜索记忆（支持模糊匹配）。"""
        resolved_top_k = self._to_int(top_k, 10)
        q = str(query or "").strip()
        tokens = self._parse_tail_tokens(event.message_str, "query")
        if tokens:
            if len(tokens) > 1:
                maybe_k = self._to_int(tokens[-1], -1, min_value=1)
                if maybe_k > 0:
                    resolved_top_k = maybe_k
                    tokens = tokens[:-1]
            parsed_query = " ".join(tokens).strip()
            if parsed_query:
                q = parsed_query
        if not q:
            yield event.plain_result("用法: /mem query <关键词> [top_k]")
            return
        self._log_cmd(event, "query", top_k=resolved_top_k, q_len=len(q))
        scope_key = self._resolve_scope(event)
        try:
            data = await self.query_service.search(scope_key=scope_key, query=q, top_k=resolved_top_k)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem query failed: %s", exc, exc_info=True)
            yield event.plain_result(f"查询失败: {exc}")

    @mem.command("time")
    async def mem_time(
        self,
        event: AstrMessageEvent,
        time_from: str = "",
        time_to: str = "",
        query: str = "",
        top_k: int = 10,
    ):
        """按时间段查找记忆（支持"昨天""上周"等自然语言）。"""
        from_text = str(time_from or "").strip()
        to_text = str(time_to or "").strip()
        query_text = str(query or "").strip()
        resolved_top_k = self._to_int(top_k, 10)

        tokens = self._parse_tail_tokens(event.message_str, "time")
        if tokens:
            from_text = str(tokens[0]).strip() or from_text
            if len(tokens) >= 2:
                to_text = str(tokens[1]).strip()
            if len(tokens) >= 3:
                query_text = " ".join(tokens[2:]).strip()

        if not from_text:
            yield event.plain_result("用法: /mem time <time_from> [time_to] [query]")
            return

        self._log_cmd(
            event,
            "time",
            time_from=from_text,
            has_time_to=bool(to_text),
            top_k=resolved_top_k,
            q_len=len(query_text),
        )
        scope_key = self._resolve_scope(event)
        try:
            data = await self.query_service.time_search(
                scope_key=scope_key,
                query=query_text,
                time_from=from_text or None,
                time_to=to_text or None,
                top_k=resolved_top_k,
            )
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem time failed: %s", exc, exc_info=True)
            yield event.plain_result(f"时序查询失败: {exc}")

    @mem.command("episode")
    async def mem_episode(
        self,
        event: AstrMessageEvent,
        query: str = "",
        top_k: int = 10,
    ):
        """按 Episode 查询记忆片段。"""
        q = str(query or "").strip()
        resolved_top_k = self._to_int(top_k, 10)
        tokens = self._parse_tail_tokens(event.message_str, "episode")
        if tokens:
            maybe_k = self._to_int(tokens[-1], -1, min_value=1)
            if len(tokens) > 1 and maybe_k > 0:
                resolved_top_k = maybe_k
                tokens = tokens[:-1]
            parsed_query = " ".join(tokens).strip()
            if parsed_query:
                q = parsed_query

        self._log_cmd(event, "episode", top_k=resolved_top_k, q_len=len(q))
        scope_key = self._resolve_scope(event)
        try:
            data = await self.query_service.episode(scope_key=scope_key, query=q, top_k=resolved_top_k)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem episode failed: %s", exc, exc_info=True)
            yield event.plain_result(f"Episode 查询失败: {exc}")

    @mem.command("aggregate")
    async def mem_aggregate(
        self,
        event: AstrMessageEvent,
        query: str = "",
        top_k: int = 10,
    ):
        """聚合查询 search/time/episode 召回结果。"""
        q = str(query or "").strip()
        resolved_top_k = self._to_int(top_k, 10)
        tokens = self._parse_tail_tokens(event.message_str, "aggregate")
        if tokens:
            maybe_k = self._to_int(tokens[-1], -1, min_value=1)
            if len(tokens) > 1 and maybe_k > 0:
                resolved_top_k = maybe_k
                tokens = tokens[:-1]
            parsed_query = " ".join(tokens).strip()
            if parsed_query:
                q = parsed_query
        if not q:
            yield event.plain_result("用法: /mem aggregate <关键词> [top_k]")
            return

        self._log_cmd(event, "aggregate", top_k=resolved_top_k, q_len=len(q))
        scope_key = self._resolve_scope(event)
        try:
            data = await self.query_service.aggregate(scope_key=scope_key, query=q, top_k=resolved_top_k)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem aggregate failed: %s", exc, exc_info=True)
            yield event.plain_result(f"聚合查询失败: {exc}")

    @mem.command("protect")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_protect(self, event: AstrMessageEvent, query_or_hash: str = "", hours: float = 24.0):
        """保护记忆不被自动衰减清理（不填时长则永久保护）。"""
        target = str(query_or_hash or "").strip()
        resolved_hours = self._to_float(hours, 24.0, min_value=0.1)
        tokens = self._parse_tail_tokens(event.message_str, "protect")
        if tokens:
            maybe_hours = self._to_float(tokens[-1], -1.0, min_value=0.1)
            if len(tokens) > 1 and maybe_hours > 0:
                resolved_hours = maybe_hours
                tokens = tokens[:-1]
            parsed_target = " ".join(tokens).strip()
            if parsed_target:
                target = parsed_target
        if not target:
            yield event.plain_result("用法: /mem protect <hash_or_query> [hours]")
            return
        self._log_cmd(event, "protect", hours=resolved_hours, target_len=len(target))
        scope_key = self._resolve_scope(event)
        try:
            data = await self.memory_service.protect(scope_key=scope_key, query_or_hash=target, hours=resolved_hours)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem protect failed: %s", exc, exc_info=True)
            yield event.plain_result(f"保护失败: {exc}")

    @mem.command("reinforce")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_reinforce(self, event: AstrMessageEvent, query_or_hash: str = ""):
        """强化记忆权重并自动保护 24 小时。"""
        target = str(query_or_hash or "").strip()
        parsed_target = self._parse_tail(event.message_str, "reinforce")
        if parsed_target:
            target = parsed_target
        if not target:
            yield event.plain_result("用法: /mem reinforce <hash_or_query>")
            return
        self._log_cmd(event, "reinforce", target_len=len(target))
        scope_key = self._resolve_scope(event)
        try:
            data = await self.memory_service.reinforce(scope_key=scope_key, query_or_hash=target)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem reinforce failed: %s", exc, exc_info=True)
            yield event.plain_result(f"强化失败: {exc}")

    @mem.command("restore")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_restore(self, event: AstrMessageEvent, hash_value: str = "", restore_type: str = "relation"):
        """从回收站恢复已被剪枝的记忆。"""
        target = str(hash_value or "").strip()
        rtype = str(restore_type or "relation").strip() or "relation"
        tokens = self._parse_tail_tokens(event.message_str, "restore")
        if tokens:
            target = str(tokens[0]).strip() or target
            if len(tokens) > 1:
                rtype = str(tokens[1]).strip() or rtype
        if not target:
            yield event.plain_result("用法: /mem restore <hash> [relation|entity]")
            return
        self._log_cmd(event, "restore", restore_type=rtype, hash=target)
        scope_key = self._resolve_scope(event)
        try:
            data = await self.memory_service.restore(scope_key=scope_key, hash_value=target, restore_type=rtype)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem restore failed: %s", exc, exc_info=True)
            yield event.plain_result(f"恢复失败: {exc}")

    @mem.command("delete_entity")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_delete_entity(self, event: AstrMessageEvent, entity_name: str = ""):
        """删除指定实体及其所有关联关系。"""
        target = str(entity_name or "").strip()
        parsed = self._parse_tail(event.message_str, "delete_entity")
        if parsed:
            target = parsed
        if not target:
            yield event.plain_result("用法: /mem delete_entity <实体名>")
            return
        self._log_cmd(event, "delete_entity", entity_len=len(target))
        scope_key = self._resolve_scope(event)
        try:
            runtime = await self.runtime_manager.get_runtime(scope_key)
            from .memorix.amemorix.services.delete_service import DeleteService

            data = await DeleteService(runtime.context).entity(target)
            data["scope"] = scope_key
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem delete_entity failed: %s", exc, exc_info=True)
            yield event.plain_result(f"删除实体失败: {exc}")

    @mem.command("profile")
    async def mem_profile(self, event: AstrMessageEvent, person_keyword_or_id: str = "", top_k: int = 12):
        """查看人物画像（自动生成的用户特征摘要）。"""
        scope_key = self._resolve_scope(event)
        keyword = str(person_keyword_or_id or "").strip()
        resolved_top_k = self._to_int(top_k, 12)
        tokens = self._parse_tail_tokens(event.message_str, "profile")
        if tokens:
            if len(tokens) > 1:
                maybe_k = self._to_int(tokens[-1], -1, min_value=1)
                if maybe_k > 0:
                    resolved_top_k = maybe_k
                    tokens = tokens[:-1]
            parsed_keyword = " ".join(tokens).strip()
            if parsed_keyword:
                keyword = parsed_keyword
        if not keyword:
            keyword = str(getattr(event, "get_sender_id", lambda: "")() or "")
        self._log_cmd(event, "profile", top_k=resolved_top_k, keyword_len=len(keyword))
        try:
            data = await self.profile_service.query(
                scope_key=scope_key,
                person_keyword=keyword,
                top_k=resolved_top_k,
                force_refresh=False,
            )
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem profile failed: %s", exc, exc_info=True)
            yield event.plain_result(f"画像查询失败: {exc}")

    @mem.command("profile_override")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_profile_override(self, event: AstrMessageEvent, person_id: str = "", override_text: str = ""):
        """手动覆盖指定用户的人物画像内容。"""
        pid = str(person_id or "").strip()
        text = str(override_text or "").strip()
        tail = self._parse_tail(event.message_str, "profile_override")
        if tail:
            parts = tail.split(maxsplit=1)
            if len(parts) == 2:
                pid = str(parts[0]).strip() or pid
                text = str(parts[1]).strip() or text
        if not pid or not text:
            yield event.plain_result("用法: /mem profile_override <person_id> <text>")
            return
        self._log_cmd(event, "profile_override", person_id=pid, text_len=len(text))
        scope_key = self._resolve_scope(event)
        try:
            data = await self.profile_service.set_override(scope_key=scope_key, person_id=pid, override_text=text)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem profile_override failed: %s", exc, exc_info=True)
            yield event.plain_result(f"画像覆盖失败: {exc}")

    @mem.command("profile_clear")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_profile_clear(self, event: AstrMessageEvent, person_id: str = ""):
        """清除手动覆盖，恢复自动生成的画像。"""
        pid = str(person_id or "").strip()
        parsed_pid = self._parse_tail(event.message_str, "profile_clear")
        if parsed_pid:
            pid = parsed_pid
        if not pid:
            yield event.plain_result("用法: /mem profile_clear <person_id>")
            return
        self._log_cmd(event, "profile_clear", person_id=pid)
        scope_key = self._resolve_scope(event)
        try:
            data = await self.profile_service.delete_override(scope_key=scope_key, person_id=pid)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem profile_clear failed: %s", exc, exc_info=True)
            yield event.plain_result(f"画像覆盖清除失败: {exc}")

    @mem.command("profile_global")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_profile_global(self, event: AstrMessageEvent, action: str = "status"):
        """全局开关：是否在 LLM 请求中注入人物画像。"""
        tokens = self._parse_tail_tokens(event.message_str, "profile_global")
        cmd_action = str(action or "").strip().lower()
        if tokens:
            cmd_action = str(tokens[0]).strip().lower()
        if not cmd_action:
            cmd_action = "status"
        self._log_cmd(event, "profile_global", action=cmd_action)
        try:
            data = await self._handle_person_profile_global_action(cmd_action)
            yield event.plain_result(to_pretty_text(data))
        except ValueError as exc:
            yield event.plain_result(str(exc))
        except Exception as exc:
            logger.error("[memorix] mem profile_global failed: %s", exc, exc_info=True)
            yield event.plain_result(f"全局画像策略设置失败: {exc}")

    @mem.command("summary_now")
    async def mem_summary_now(self, event: AstrMessageEvent, context_length: int = 50):
        """立即对当前对话生成 AI 总结并写入记忆。"""
        resolved_context_length = self._to_int(context_length, 50)
        tokens = self._parse_tail_tokens(event.message_str, "summary_now")
        if tokens:
            parsed = self._to_int(tokens[0], -1)
            if parsed > 0:
                resolved_context_length = parsed
        self._log_cmd(event, "summary_now", context_length=resolved_context_length)
        scope_key = self._resolve_scope(event)
        adapted = AstrbotEventAdapter.from_event(event, scope_key)
        try:
            data = await self.summary_service.summarize_session(
                scope_key=scope_key,
                session_id=adapted.session_id,
                source=f"chat_summary:{adapted.session_id}",
                context_length=resolved_context_length,
            )
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem summary_now failed: %s", exc, exc_info=True)
            yield event.plain_result(f"总结失败: {exc}")

    @mem.command("summary_all")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def mem_summary_all(self, event: AstrMessageEvent, context_length: int = 50, limit: int = 500):
        """批量总结当前作用域内所有会话的历史记录。"""
        resolved_context_length = self._to_int(context_length, 50)
        resolved_limit = self._to_int(limit, 500)
        tokens = self._parse_tail_tokens(event.message_str, "summary_all")
        if tokens:
            resolved_context_length = self._to_int(tokens[0], resolved_context_length)
            if len(tokens) > 1:
                resolved_limit = self._to_int(tokens[1], resolved_limit)

        self._log_cmd(event, "summary_all", context_length=resolved_context_length, limit=resolved_limit)
        scope_key = self._resolve_scope(event)
        started_at = time.time()
        try:
            runtime = await self.runtime_manager.get_runtime(scope_key)
            data = await runtime.task_manager.run_bulk_summary_import(
                context_length=resolved_context_length,
                limit=resolved_limit,
            )
            data["scope"] = scope_key
            data["elapsed_seconds"] = round(time.time() - started_at, 3)
            yield event.plain_result(to_pretty_text(data))
        except Exception as exc:
            logger.error("[memorix] mem summary_all failed: %s", exc, exc_info=True)
            yield event.plain_result(f"全量总结失败: {exc}")
