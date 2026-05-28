import asyncio

import numpy as np

from astrbot_plugin_memorix.memorix.core.storage.metadata_store import MetadataStore
from astrbot_plugin_memorix.memorix.core.utils.summary_importer import SummaryImporter


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
        for node in nodes:
            if node not in self.nodes:
                self.nodes.append(node)
        return len(nodes)

    def add_edges(self, edges, weights=None, relation_hashes=None):
        del weights, relation_hashes
        for source, target in edges:
            self.edges.append((source, target))
        return len(edges)

    def save(self):
        return None


class _FakeEmbeddingManager:
    async def encode(self, _text):
        return np.ones((4,), dtype=np.float32)


class _RoleEntityLLM:
    async def complete_json(self, prompt, temperature=0.2, max_tokens=1200):
        del prompt, temperature, max_tokens
        return (
            True,
            {
                "summary": "用户喜欢 RPG。",
                "entities": ["用户", "RPG"],
                "relations": [{"subject": "用户", "predicate": "喜欢", "object": "RPG"}],
            },
            "",
        )


def test_summary_import_keeps_llm_entities_and_derives_time_meta(tmp_path):
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
            llm_client=_RoleEntityLLM(),
        )

        ok, message = asyncio.run(
            importer.import_from_transcript(
                session_id="s1",
                messages=[
                    {
                        "role": "user",
                        "content": "我喜欢 RPG",
                        "timestamp": 100.0,
                        "metadata": {"sender_name": "小明", "sender_id": "u1", "platform": "qq"},
                    },
                    {"role": "assistant", "content": "我记住了", "timestamp": 160.0},
                ],
                source="chat_summary:s1",
                context_length=5,
            )
        )

        assert ok is True, message
        assert "用户" in graph_store.nodes
        assert ("用户", "RPG") in graph_store.edges
        assert len(metadata_store.get_relations(subject="用户", object="RPG")) == 1

        paragraphs = metadata_store.get_paragraphs_by_source("chat_summary:s1")
        assert len(paragraphs) == 1
        assert paragraphs[0]["event_time_start"] == 100.0
        assert paragraphs[0]["event_time_end"] == 160.0
        assert paragraphs[0]["time_granularity"] == "minute"
        assert paragraphs[0]["time_confidence"] == 0.95
    finally:
        metadata_store.close()
