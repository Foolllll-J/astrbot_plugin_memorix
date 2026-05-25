from types import SimpleNamespace

from astrbot_plugin_memorix.main import MemorixPlugin
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


def test_plugin_registers_and_removes_llm_tools():
    ctx = DummyContext()
    plugin = MemorixPlugin(ctx, {"scope": {"mode": "group_global"}})

    # Avoid starting the embedded WebUI bridge in this unit test.
    plugin.webui_page_bridge.register = lambda *args, **kwargs: None

    import asyncio

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
