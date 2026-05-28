"""Entity cleanup helpers for graph-facing extracted knowledge."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


_USER_PLACEHOLDERS = {
    "user",
    "the user",
    "current user",
    "speaker",
    "current speaker",
    "用户",
    "当前用户",
    "该用户",
    "这位用户",
    "使用者",
    "说话人",
    "当前说话人",
    "我",
    "我的",
    "本人",
    "自己",
}

_ASSISTANT_PLACEHOLDERS = {
    "assistant",
    "the assistant",
    "current assistant",
    "bot",
    "助手",
    "助理",
    "当前助手",
    "该助手",
    "机器人助手",
    "ai助手",
    "ai 助手",
}

_SYSTEM_PLACEHOLDERS = {
    "system",
    "tool",
    "系统",
    "工具",
}

_ROLE_PLACEHOLDERS = _USER_PLACEHOLDERS | _ASSISTANT_PLACEHOLDERS | _SYSTEM_PLACEHOLDERS


def _entity_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def is_role_placeholder_entity(value: Any) -> bool:
    """Return whether *value* is a chat role/pronoun, not a stable graph entity."""

    return _entity_key(value) in _ROLE_PLACEHOLDERS


def _is_user_placeholder(value: Any) -> bool:
    return _entity_key(value) in _USER_PLACEHOLDERS


def _is_assistant_placeholder(value: Any) -> bool:
    return _entity_key(value) in _ASSISTANT_PLACEHOLDERS


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = _entity_key(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _message_metadata(message: Dict[str, Any]) -> Dict[str, Any]:
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        merged = dict(metadata)
        merged.update(nested)
        return merged
    return metadata


def message_speaker_identity(message: Dict[str, Any]) -> str:
    """Resolve a stable speaker identity from transcript message metadata."""

    metadata = _message_metadata(message)
    for key in ("sender_name", "person_name", "nickname", "group_nick_name"):
        candidate = str(message.get(key) or metadata.get(key) or "").strip()
        if candidate and not is_role_placeholder_entity(candidate):
            return candidate

    platform = str(message.get("platform") or metadata.get("platform") or "").strip()
    sender_id = str(
        message.get("sender_id")
        or message.get("user_id")
        or metadata.get("sender_id")
        or metadata.get("user_id")
        or ""
    ).strip()
    if sender_id:
        return f"{platform}:{sender_id}" if platform else sender_id
    return ""


def collect_user_speakers(messages: Iterable[Dict[str, Any]]) -> List[str]:
    """Collect stable user speaker names/IDs from normalized transcript messages."""

    speakers: List[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "user") or "user").strip().lower()
        if role != "user":
            continue
        identity = message_speaker_identity(message)
        if identity:
            speakers.append(identity)
    return _dedupe(speakers)


def resolve_role_placeholder_entity(
    value: Any,
    *,
    user_speakers: Iterable[str] = (),
    bot_name: str = "",
) -> Optional[str]:
    """
    Resolve role placeholders to concrete identities when safe.

    - A user placeholder is mapped only when exactly one user speaker is known.
    - An assistant placeholder is mapped only when bot_name is concrete.
    - System/tool placeholders are always dropped.
    """

    text = str(value or "").strip()
    if not text:
        return None
    if _is_user_placeholder(text):
        speakers = _dedupe(user_speakers)
        return speakers[0] if len(speakers) == 1 else None
    if _is_assistant_placeholder(text):
        bot = str(bot_name or "").strip()
        return bot if bot and not is_role_placeholder_entity(bot) else None
    if is_role_placeholder_entity(text):
        return None
    return text


def sanitize_extracted_entities_relations(
    entities: Any,
    relations: Any,
    *,
    user_speakers: Iterable[str] = (),
    bot_name: str = "",
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Filter or remap role placeholders from LLM/tool extracted graph data."""

    sanitized_entities = _dedupe(
        resolved
        for item in (entities if isinstance(entities, list) else [])
        if (resolved := resolve_role_placeholder_entity(item, user_speakers=user_speakers, bot_name=bot_name))
    )

    sanitized_relations: List[Dict[str, Any]] = []
    seen_relations = set()
    if not isinstance(relations, list):
        return sanitized_entities, sanitized_relations

    for item in relations:
        if not isinstance(item, dict):
            continue
        subject = resolve_role_placeholder_entity(
            item.get("subject"),
            user_speakers=user_speakers,
            bot_name=bot_name,
        )
        obj = resolve_role_placeholder_entity(
            item.get("object", item.get("obj")),
            user_speakers=user_speakers,
            bot_name=bot_name,
        )
        predicate = str(item.get("predicate", "") or "").strip()
        if not (subject and predicate and obj):
            continue

        key = (_entity_key(subject), _entity_key(predicate), _entity_key(obj))
        if key in seen_relations:
            continue
        seen_relations.add(key)

        row = dict(item)
        row["subject"] = subject
        row["predicate"] = predicate
        row["object"] = obj
        row.pop("obj", None)
        sanitized_relations.append(row)

    return sanitized_entities, sanitized_relations
