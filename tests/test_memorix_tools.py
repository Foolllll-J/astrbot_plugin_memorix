from types import SimpleNamespace

from astrbot_plugin_memorix.main import MemorixPlugin
from astrbot_plugin_memorix.memorix.tools import build_memorix_tools


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


def test_build_memorix_tools_matches_maibot_core_names():
    names = [tool.name for tool in build_memorix_tools(DummyPlugin())]
    assert names == [
        "search_memory",
        "ingest_summary",
        "ingest_text",
        "get_person_profile",
        "maintain_memory",
        "memory_stats",
    ]


def test_plugin_registers_and_removes_llm_tools():
    ctx = DummyContext()
    plugin = MemorixPlugin(ctx, {"scope": {"mode": "group_global"}})

    # Avoid starting the embedded WebUI bridge in this unit test.
    plugin.webui_page_bridge.register = lambda *args, **kwargs: None

    import asyncio

    asyncio.run(plugin.initialize())
    assert [tool.name for tool in ctx.added] == [
        "search_memory",
        "ingest_summary",
        "ingest_text",
        "get_person_profile",
        "maintain_memory",
        "memory_stats",
    ]

    plugin._remove_llm_tools()
    assert ctx.removed == [tool.name for tool in ctx.added]
