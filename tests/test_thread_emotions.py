"""Regression coverage for per-thread emotional state and public auth gates."""

from types import SimpleNamespace


def _reset_endocrine(module, path):
    module.ENDO_FILE = str(path)
    module._states = {"main": module._default_state()}
    module._state = module._states["main"]


def _reset_drives(module, path):
    module.DRIVES_FILE = str(path)
    module._states = {"main": module._default_state()}
    module._state = module._states["main"]


def test_endocrine_isolated_and_persistent(tmp_path):
    import endocrine

    path = tmp_path / "endocrine.json"
    _reset_endocrine(endocrine, path)
    endocrine.set_levels(thread="main", libido=9, dominance=9)

    assert endocrine.state("main")["libido"] == 9
    assert endocrine.state("if_training")["libido"] == endocrine.BASELINE["libido"]

    endocrine.set_levels(thread="if_training", energy=2, affection=4)
    endocrine.load()
    assert endocrine.state("main")["libido"] == 9
    assert endocrine.state("if_training")["energy"] == 2
    assert endocrine.state("if_training")["affection"] == 4


def test_drives_isolated_and_persistent(tmp_path):
    import drives

    path = tmp_path / "drives.json"
    _reset_drives(drives, path)
    drives.update("别人家的男生", thread="main")
    main_jealousy = drives.state("main")["v"]["jealousy"]

    assert drives.state("if_training")["v"]["jealousy"] == drives.NEUTRAL["jealousy"]
    drives.update("爱你抱抱", thread="if_training")
    drives.load()
    assert drives.state("main")["v"]["jealousy"] == main_jealousy
    assert drives.state("if_training")["v"]["intimacy"] > drives.NEUTRAL["intimacy"]


def test_sensitive_gate_does_not_trust_missing_forwarded_header(monkeypatch):
    import server

    monkeypatch.setenv("OMBRE_HOME_PASSWORD", "secret")
    monkeypatch.setenv("OMBRE_WEB_TOKEN", "web-token")
    public = SimpleNamespace(
        headers={}, cookies={}, query_params={}, client=SimpleNamespace(host="203.0.113.9")
    )
    local = SimpleNamespace(
        headers={}, cookies={}, query_params={}, client=SimpleNamespace(host="127.0.0.1")
    )
    bearer = SimpleNamespace(
        headers={"authorization": "Bearer web-token", "x-forwarded-for": "203.0.113.9"},
        cookies={}, query_params={}, client=SimpleNamespace(host="127.0.0.1"),
    )

    assert server._sensitive_gate(public) is False
    assert server._sensitive_gate(local) is True
    assert server._sensitive_gate(bearer) is True
