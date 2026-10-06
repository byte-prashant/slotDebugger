import pytest

import think


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep tests off the real .think_state.json."""
    path = str(tmp_path / "state.json")
    monkeypatch.setattr(think, "STATE_FILE", path)
    test_module = __import__("sys").modules.get("test_think")
    if test_module is not None:
        monkeypatch.setattr(test_module, "STATE_FILE", path)
    return path
