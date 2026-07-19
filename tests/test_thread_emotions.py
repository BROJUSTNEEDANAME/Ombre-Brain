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


def test_visual_state_uses_both_dominance_and_possessiveness(tmp_path):
    import drives
    import endocrine
    import server

    _reset_endocrine(endocrine, tmp_path / "endocrine.json")
    _reset_drives(drives, tmp_path / "drives.json")
    endocrine.set_levels(thread="if_a", dominance=8.6, libido=3)
    high_dom = server._endo_view("if_a")
    assert high_dom["glow"] is True
    assert high_dom["dim"] is True
    assert "支配" in high_dom["visual_reason"]

    endocrine.set_levels(thread="if_b", dominance=3, libido=3)
    drives._get_state("if_b")["v"]["possessiveness"] = 0.82
    high_poss = server._endo_view("if_b")
    assert high_poss["glow"] is True
    assert high_poss["dim"] is True
    assert high_poss["dominant"] == "占有"
    assert "占有" in high_poss["visual_reason"]


def test_calm_resets_both_visual_state_layers(tmp_path):
    import drives
    import endocrine
    import server

    _reset_endocrine(endocrine, tmp_path / "endocrine.json")
    _reset_drives(drives, tmp_path / "drives.json")
    endocrine.set_levels(thread="main", dominance=10, libido=10)
    drives._get_state("main")["v"]["possessiveness"] = 1.0
    drives._get_state("main")["v"]["lust"] = 1.0

    endocrine.calm("main")
    drives.calm("main")
    calm = server._endo_view("main")

    assert calm["dim"] is False
    assert calm["glow"] is False
    assert calm["possessiveness"] == drives.NEUTRAL["possessiveness"]
    assert calm["lust_drive"] == drives.NEUTRAL["lust"]


def test_structured_if_worldbook_is_injected():
    import server

    block = server._if_static_block({
        "name": "雪原线",
        "scenario": "两人在废弃哨站醒来。",
        "hooks": "无线电里有一个不该存在的呼号。",
        "lore_entries": [{"title": "北塔", "keys": "塔,无线电", "content": "午夜才会亮灯。", "enabled": True}],
    })
    assert "当前场景" in block and "废弃哨站" in block
    assert "北塔" in block and "午夜才会亮灯" in block
    assert "可探索内容" in block and "呼号" in block
    assert "Nikto/Svyatoslav 始终" in block
