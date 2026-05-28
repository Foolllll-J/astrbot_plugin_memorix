import asyncio
import sys
import types
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_astrbot_stub() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    api_mod.logger = _Logger()
    astrbot_mod.api = api_mod
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod


_install_astrbot_stub()

from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore  # noqa: E402
from astrbot_plugin_memorix.memorix.core.utils.summary_importer import SummaryImporter  # noqa: E402
from astrbot_plugin_memorix.memorix.webui.routes_compat import MemorixServer  # noqa: E402


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


class _RecordingGraphStore:
    def __init__(self):
        self.nodes = []
        self.edges = []

    def add_nodes(self, nodes):
        added = 0
        for node in nodes:
            if node not in self.nodes:
                self.nodes.append(node)
                added += 1
        return added

    def add_edges(self, edges, weights=None, relation_hashes=None):
        del weights, relation_hashes
        for source, target in edges:
            self.add_nodes([source, target])
            self.edges.append((source, target))
        return len(edges)

    def save(self):
        return None


class _FakeEmbeddingManager:
    async def encode(self, _text):
        return np.ones((4,), dtype=np.float32)


class _RolePlaceholderLLM:
    async def complete_json(self, prompt, temperature=0.2, max_tokens=1200):
        del prompt, temperature, max_tokens
        return (
            True,
            {
                "summary": "小明喜欢 RPG。",
                "entities": ["用户", "RPG"],
                "relations": [{"subject": "用户", "predicate": "喜欢", "object": "RPG"}],
            },
            "",
        )


def test_summary_importer_maps_user_placeholder_to_sender(tmp_path):
    metadata_store = MetadataStore(tmp_path)
    metadata_store.connect()
    graph_store = _RecordingGraphStore()
    try:
        importer = SummaryImporter(
            vector_store=_FakeVectorStore(),
            graph_store=graph_store,
            metadata_store=metadata_store,
            embedding_manager=_FakeEmbeddingManager(),
            plugin_config={"summarization": {"default_knowledge_type": "narrative"}},
            llm_client=_RolePlaceholderLLM(),
        )

        ok, message = asyncio.run(
            importer.import_from_transcript(
                session_id="s1",
                messages=[
                    {
                        "role": "user",
                        "content": "我喜欢 RPG",
                        "metadata": {"sender_name": "小明", "sender_id": "u1", "platform": "qq"},
                    }
                ],
                source="chat_summary:s1",
                context_length=5,
            )
        )

        assert ok is True, message
        assert "用户" not in graph_store.nodes
        assert ("小明", "RPG") in graph_store.edges
        assert metadata_store.get_relations(subject="用户") == []
        assert len(metadata_store.get_relations(subject="小明", object="RPG")) == 1
    finally:
        metadata_store.close()


class _GraphForWebUI:
    def __init__(self):
        self._nodes = ["用户", "Alice", "RPG"]
        self._node_to_idx = {self._canonicalize(node): index for index, node in enumerate(self._nodes)}
        self._neighbors = {"用户": ["RPG"], "Alice": ["RPG"], "RPG": []}
        self._weights = {("用户", "RPG"): 10.0, ("Alice", "RPG"): 1.0}
        self._edge_hash_map = {
            (self._node_to_idx["用户"], self._node_to_idx["rpg"]): {"h_user"},
            (self._node_to_idx["alice"], self._node_to_idx["rpg"]): {"h_alice"},
        }

    def _canonicalize(self, node):
        return str(node or "").strip().lower()

    def get_nodes(self):
        return list(self._nodes)

    def get_saliency_scores(self):
        return {"用户": 10.0, "Alice": 1.0, "RPG": 1.0}

    def get_neighbors(self, source):
        return list(self._neighbors.get(source, []))

    def get_edge_weight(self, source, target):
        return self._weights.get((source, target), 0.0)

    def get_relation_hashes_for_edge(self, source, target):
        source_idx = self._node_to_idx[self._canonicalize(source)]
        target_idx = self._node_to_idx[self._canonicalize(target)]
        return set(self._edge_hash_map.get((source_idx, target_idx), set()))


class _MetadataForWebUI:
    def get_all_triples(self):
        return [
            ("用户", "喜欢", "RPG", "h_user"),
            ("Alice", "喜欢", "RPG", "h_alice"),
        ]

    def get_entity_status_batch(self, _hashes):
        return {}

    def get_relation_status_batch(self, hashes):
        return {
            item: {"is_inactive": False, "is_pinned": False, "protected_until": 0}
            for item in hashes
        }


class _PluginForWebUI:
    def __init__(self):
        self.graph_store = _GraphForWebUI()
        self.metadata_store = _MetadataForWebUI()
        self.vector_store = object()
        self.embedding_manager = object()
        self.config = {}

    def get_config(self, _key, default=None):
        return default


def test_webui_graph_omits_role_placeholder_hub():
    server = MemorixServer(_PluginForWebUI())
    response = TestClient(server.app).get("/api/graph")

    assert response.status_code == 200
    payload = response.json()
    node_ids = {item["id"] for item in payload["nodes"]}
    assert "用户" not in node_ids
    assert {"Alice", "RPG"} <= node_ids
    assert all(edge["from"] != "用户" and edge["to"] != "用户" for edge in payload["edges"])
    assert any(edge["from"] == "Alice" and edge["to"] == "RPG" for edge in payload["edges"])
