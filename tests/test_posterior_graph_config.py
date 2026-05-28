from astrbot_plugin_memorix.memorix.core.retrieval import DualPathRetrieverConfig, PosteriorGraphConfig
from astrbot_plugin_memorix.memorix.core.retrieval.posterior_graph import find_score_cliff


class _Result:
    def __init__(self, score):
        self.score = score


def test_posterior_graph_config_is_wired_into_retriever_config():
    cfg = DualPathRetrieverConfig(posterior_graph={"enabled": False, "max_graph_slots": 3})

    assert isinstance(cfg.posterior_graph, PosteriorGraphConfig)
    assert cfg.posterior_graph.enabled is False
    assert cfg.posterior_graph.max_graph_slots == 3


def test_find_score_cliff_keeps_core_before_large_drop():
    results = [_Result(1.0), _Result(0.92), _Result(0.40), _Result(0.39)]

    assert find_score_cliff(results, drop_ratio=0.15, min_core_results=2) == 2
