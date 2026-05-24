import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_astrbot_stub() -> None:
    if "astrbot.api" in sys.modules:
        return
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    core_mod = types.ModuleType("astrbot.core")
    utils_mod = types.ModuleType("astrbot.core.utils")
    path_mod = types.ModuleType("astrbot.core.utils.astrbot_path")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    def get_astrbot_data_path(*args, **kwargs):
        del args, kwargs
        return str(ROOT / ".test-astrbot-data")

    api_mod.logger = _Logger()
    astrbot_mod.api = api_mod
    astrbot_mod.core = core_mod
    core_mod.utils = utils_mod
    utils_mod.astrbot_path = path_mod
    path_mod.get_astrbot_data_path = get_astrbot_data_path
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.core"] = core_mod
    sys.modules["astrbot.core.utils"] = utils_mod
    sys.modules["astrbot.core.utils.astrbot_path"] = path_mod


def _install_openai_stub() -> None:
    if "openai" in sys.modules:
        return

    openai_mod = types.ModuleType("openai")

    class AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            del args, kwargs

    openai_mod.AsyncOpenAI = AsyncOpenAI
    sys.modules["openai"] = openai_mod


_install_astrbot_stub()
_install_openai_stub()
