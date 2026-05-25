import asyncio
from types import SimpleNamespace

import numpy as np

from astrbot_plugin_memorix.main import MemorixPlugin
from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.services.content_router import MemoryContentRouter
from astrbot_plugin_memorix.memorix.services.person_fact_writeback_service import (
    PersonFactWritebackItem,
    PersonFactWritebackService,
)
from astrbot_plugin_memorix.memorix.tools import _format_search_result_for_llm, build_memorix_tools


class DummyContext:
    def __init__(self):
        self.added = []
        self.removed = []
        self._tool_manager = SimpleNamespace(remove_func=self.removed.append)

    def add_llm_tools(self, *tools):
        self.added.extend(tools)

    def get_llm_tool_manager(self):
        return self._tool_manager


class DummyPlugin:
    def _resolve_scope(self, event):
        del event
        return "group:test"


EXPECTED_TOOL_NAMES = [
    "search_memory",
    "ingest_summary",
    "ingest_text",
    "get_person_profile",
    "maintain_memory",
    "memory_stats",
    "memory_graph_admin",
    "memory_source_admin",
    "memory_episode_admin",
    "memory_profile_admin",
    "memory_runtime_admin",
    "memory_import_admin",
    "memory_tuning_admin",
    "memory_v5_admin",
    "memory_delete_admin",
]


def test_build_memorix_tools_matches_maibot_core_names():
    names = [tool.name for tool in build_memorix_tools(DummyPlugin())]
    assert names == EXPECTED_TOOL_NAMES


def test_search_memory_tool_schema_guides_time_modes():
    tools = build_memorix_tools(DummyPlugin())
    search_tool = next(tool for tool in tools if tool.name == "search_memory")

    assert "默认使用 mode=search" in search_tool.description
    assert "time/hybrid 必须" in search_tool.description
    assert "不会自动降级" in search_tool.description
    assert "没有时间条件时不要用 hybrid/time" in search_tool.parameters["properties"]["mode"]["description"]
    assert "至少填它或 time_end" in search_tool.parameters["properties"]["time_start"]["description"]
    assert "至少填它或 time_start" in search_tool.parameters["properties"]["time_end"]["description"]


def test_plugin_registers_and_removes_llm_tools():
    ctx = DummyContext()
    plugin = MemorixPlugin(ctx, {"scope": {"mode": "group_global"}})

    # Avoid starting the embedded WebUI bridge in this unit test.
    plugin.webui_page_bridge.register = lambda *args, **kwargs: None

    asyncio.run(plugin.initialize())
    assert [tool.name for tool in ctx.added] == EXPECTED_TOOL_NAMES

    plugin._remove_llm_tools()
    assert ctx.removed == [tool.name for tool in ctx.added]


def test_search_memory_result_is_formatted_for_llm():
    text = _format_search_result_for_llm(
        {
            "query_type": "search",
            "query": "自亦飞瑶",
            "count": 2,
            "results": [
                {
                    "hash": "a9f54621d5a34581bfe6ce3cf89e099d24e3070dd7759d5dadf451fa9a4c30db",
                    "type": "paragraph",
                    "score": 0.5,
                    "content": "小真寻表示不知自亦飞瑶是谁。",
                    "metadata": {
                        "time_meta": {
                            "effective_start_text": "2026/05/21 12:48",
                            "effective_end_text": "2026/05/21 12:48",
                        }
                    },
                },
                {
                    "hash": "12130686cbb3c531c5d3b5c34cf09563d85a185b8c2d5e1fd3a2ac68ab513bb4",
                    "type": "paragraph",
                    "score": 0.0,
                    "content": "成员询问自亦飞瑶，未查到具体行为。",
                },
            ],
            "scope": "aiocqhttp",
            "chat_id": "722568590",
        },
        limit=10,
    )

    assert "【Memorix 长期记忆检索结果】" in text
    assert "命中：2 条" in text
    assert "证据列表：" in text
    assert "不要编造" in text
    assert "小真寻表示不知自亦飞瑶是谁" in text
    assert "未找到匹配的长期记忆" not in text


def test_content_router_auto_directs_fact_candidate_only():
    router = MemoryContentRouter({"ingest": {"memory_write_mode": "auto"}})
    fact_route = router.route_message(role="user", text="我喜欢深夜打游戏，也经常玩 RPG")
    chat_route = router.route_message(role="user", text="今天这个天气真不错")
    assistant_route = router.route_message(role="assistant", text="我喜欢帮你记录信息")

    assert fact_route.store_transcript is True
    assert fact_route.write_direct is True
    assert fact_route.reason == "auto_fact_candidate"
    assert chat_route.write_direct is False
    assert assistant_route.write_direct is False


def test_content_router_can_drop_ephemeral_transcript():
    router = MemoryContentRouter(
        {"ingest": {"memory_write_mode": "auto", "content_router": {"drop_ephemeral_transcript": True}}}
    )
    route = router.route_message(role="user", text="哈哈")
    assert route.store_transcript is False
    assert route.write_direct is False
    assert route.reason == "ephemeral"


class _FakeRuntimeManager:
    def __init__(self, ctx):
        self.ctx = ctx

    async def get_runtime(self, _scope_key):
        return SimpleNamespace(context=self.ctx)


class _FakeVectorStore:
    def __init__(self):
        self.ids = set()

    def __contains__(self, item):
        return item in self.ids

    def add(self, vectors, ids):
        del vectors
        self.ids.update(ids)

    def save(self):
        return None


class _FakeGraphStore:
    def save(self):
        return None


class _FakeEmbeddingManager:
    async def encode(self, _text):
        return np.ones((4,), dtype=np.float32)


class _StaticFactService(PersonFactWritebackService):
    async def _complete(self, ctx, prompt):
        del ctx, prompt
        return '["小明喜欢深夜打游戏"]'


def test_person_fact_writeback_stores_paragraph_and_registry_points(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    try:
        ctx = SimpleNamespace(
            metadata_store=metadata_store,
            vector_store=_FakeVectorStore(),
            graph_store=_FakeGraphStore(),
            embedding_manager=_FakeEmbeddingManager(),
            provider_bridge=None,
            llm_client=None,
        )
        service = _StaticFactService(
            _FakeRuntimeManager(ctx),
            {
                "person_fact_writeback": {
                    "enabled": True,
                    "update_registry_memory_points": True,
                }
            },
        )
        item = PersonFactWritebackItem(
            scope_key="default",
            session_id="s1",
            user_text="我喜欢深夜打游戏",
            assistant_text="我记住了你喜欢深夜打游戏。",
            user_id="u1",
            platform="qq",
            sender_name="小明",
            message_id="m1",
            timestamp=123.0,
        )

        asyncio.run(service._handle_item(item))

        record = metadata_store.get_person_registry("qq:u1")
        assert record is not None
        assert "小明喜欢深夜打游戏" in record["memory_points"]
        paragraphs = metadata_store.get_paragraphs_by_source("person_fact:s1:qq:u1")
        assert len(paragraphs) == 1
        assert "小明喜欢深夜打游戏" in paragraphs[0]["content"]
    finally:
        metadata_store.close()
