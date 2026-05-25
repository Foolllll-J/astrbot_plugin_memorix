"""Content-aware routing for automatic memory writes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict

_EPHEMERAL_RE = re.compile(
    r"^(?:哈+|哈哈+|好+的?|收到|嗯+|哦+|ok|OK|谢谢|谢了|晚安|早安|拜拜|再见|在吗|\?|？|。|\.|~)+$"
)
_PLACEHOLDER_RE = re.compile(r"^\s*\[(?:图片|表情|表情包|语音消息|视频|转发消息|文件|JSON消息|image|voice|video|file)[^\]]*\]\s*$", re.I)
_FACT_MARKERS = (
    "我是",
    "我叫",
    "我的名字",
    "我喜欢",
    "我讨厌",
    "我不喜欢",
    "我想要",
    "我希望",
    "我习惯",
    "我家",
    "我住",
    "我在",
    "我来自",
    "我的生日",
    "我的职业",
    "我的工作",
    "我养",
    "我有一",
    "记住",
    "别忘了",
)


@dataclass(slots=True)
class MemoryRoute:
    """Decision for one normalized chat message."""

    store_transcript: bool = True
    write_direct: bool = False
    fact_candidate: bool = False
    route: str = "transcript"
    reason: str = "default"


class MemoryContentRouter:
    """Small, deterministic router before expensive LLM writeback.

    It keeps the old global ``memory_write_mode`` behavior by default and only
    adds content-aware direct writes when the mode is explicitly set to ``auto``.
    """

    def __init__(self, plugin_config: Dict[str, Any] | None = None) -> None:
        self.plugin_config = plugin_config or {}

    @staticmethod
    def _nested(config: Dict[str, Any], key: str, default: Any = None) -> Any:
        current: Any = config
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self._nested(self.plugin_config, key, default)

    def _enabled(self) -> bool:
        return bool(self._cfg("ingest.content_router.enabled", True))

    def _write_mode(self) -> str:
        mode = str(self._cfg("ingest.memory_write_mode", "transcript_only") or "transcript_only").strip().lower()
        if mode not in {"direct", "transcript_only", "both", "auto"}:
            return "transcript_only"
        return mode

    def _assistant_direct_enabled(self) -> bool:
        return bool(self._cfg("ingest.direct_write_assistant", True))

    def _drop_ephemeral_transcript(self) -> bool:
        return bool(self._cfg("ingest.content_router.drop_ephemeral_transcript", False))

    def _auto_direct_min_chars(self) -> int:
        try:
            return max(1, int(self._cfg("ingest.content_router.auto_direct_min_chars", 12) or 12))
        except (TypeError, ValueError):
            return 12

    def route_message(self, *, role: str, text: str, metadata: Dict[str, Any] | None = None) -> MemoryRoute:
        del metadata
        content = str(text or "").strip()
        normalized_role = str(role or "user").strip().lower() or "user"
        if not content:
            return MemoryRoute(store_transcript=False, write_direct=False, route="discard", reason="empty")

        if not self._enabled():
            return MemoryRoute(
                store_transcript=True,
                write_direct=self._legacy_direct_enabled(normalized_role),
                fact_candidate=self._looks_fact_like(content),
                route="legacy",
                reason="router_disabled",
            )

        ephemeral = self._looks_ephemeral(content)
        placeholder_only = self._looks_placeholder_only(content)
        if (ephemeral or placeholder_only) and self._drop_ephemeral_transcript():
            return MemoryRoute(store_transcript=False, write_direct=False, route="discard", reason="ephemeral")

        fact_candidate = normalized_role == "user" and self._looks_fact_like(content) and not ephemeral
        mode = self._write_mode()
        write_direct = False
        reason = mode

        if mode in {"direct", "both"}:
            write_direct = normalized_role != "assistant" or self._assistant_direct_enabled()
        elif mode == "auto":
            write_direct = fact_candidate and len(content) >= self._auto_direct_min_chars()
            reason = "auto_fact_candidate" if write_direct else "auto_transcript"

        return MemoryRoute(
            store_transcript=True,
            write_direct=write_direct,
            fact_candidate=fact_candidate,
            route="direct" if write_direct else "transcript",
            reason=reason,
        )

    def _legacy_direct_enabled(self, role: str) -> bool:
        mode = self._write_mode()
        if mode == "transcript_only":
            return False
        if mode == "auto":
            return False
        if role == "assistant":
            return self._assistant_direct_enabled()
        return True

    @staticmethod
    def _looks_ephemeral(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return True
        if len(content) <= 10 and _EPHEMERAL_RE.match(content):
            return True
        return False

    @staticmethod
    def _looks_placeholder_only(text: str) -> bool:
        return bool(_PLACEHOLDER_RE.match(str(text or "").strip()))

    @staticmethod
    def _looks_fact_like(text: str) -> bool:
        content = str(text or "").strip()
        if len(content) < 4:
            return False
        if any(marker in content for marker in _FACT_MARKERS):
            return True
        return False
