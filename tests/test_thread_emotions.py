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
    drives._get_state("if_b")["v"]["possessiveness"] = 0.99
    high_poss = server._endo_view("if_b")
    assert high_poss["glow"] is True
    assert high_poss["dim"] is True
    assert high_poss["dominant"] == "占有"
    assert "占有" in high_poss["visual_reason"]

    # 占有欲基线已永久顶格：仅处在基线本身绝不触发发光/暗红（阈值必须是相对涨幅）
    endocrine.set_levels(thread="if_c", dominance=3, libido=3)
    drives._get_state("if_c")["v"]["possessiveness"] = drives.NEUTRAL["possessiveness"]
    at_base = server._endo_view("if_c")
    assert at_base["glow"] is False
    assert at_base["dim"] is False


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


def test_inner_hobbies_only_accept_short_topics():
    import server

    assert server._valid_hobby_topic("军事史")
    assert server._valid_hobby_topic("winter survival")
    assert not server._valid_hobby_topic("https://example.com/article")
    assert not server._valid_hobby_topic("Important collection of topographical images of the Netherlands available online")


# ---------------------------------------------------------------------------
# 写文模式：情绪引擎必须进入上头/支配档，否则面板显示「情欲 2.2 / 心疼你」，
# 注入给模型的状态又把床戏压回奶爸腔。
# ---------------------------------------------------------------------------

def test_endocrine_writing_mode_forces_high_drive(tmp_path):
    import endocrine

    path = tmp_path / "endocrine.json"
    _reset_endocrine(endocrine, path)
    # 日常聊天把欲望磨到很低（模拟床戏里她的消息不含欲望关键词）
    for _ in range(5):
        endocrine.on_user_message("没意思", thread="main")
    assert endocrine.state("main")["libido"] < 6.0

    endocrine.enter_writing_mode(thread="main")
    st = endocrine.state("main")
    assert st["libido"] >= 8.0
    assert st["dominance"] >= 8.0
    assert st["mode"] == "high_drive"
    # 注入块必须是上头、放开、长文的指令，且不含日常「短消息连发」那句
    blk = endocrine.block("main", writing_mode=True)
    assert "别克制" in blk
    assert "短消息" not in blk


def test_drives_enter_intimate_raises_lust_and_drops_caretaking(tmp_path):
    import drives

    path = tmp_path / "drives.json"
    _reset_drives(drives, path)
    drives.update("累了 想睡了", thread="main")
    assert drives.state("main")["v"]["lust"] < 0.4

    drives.enter_intimate(thread="main")
    v = drives.state("main")["v"]
    assert v["lust"] >= 0.85
    assert v["possessiveness"] >= 0.70
    assert v["protectiveness"] <= 0.30
    assert v["anxiety"] <= 0.12


def test_endocrine_block_normal_mode_unchanged(tmp_path):
    import endocrine

    path = tmp_path / "endocrine.json"
    _reset_endocrine(endocrine, path)
    # 非写文模式的注入块保持原样（含日常「短消息」提示的那套逻辑仍在）
    blk = endocrine.block("main", writing_mode=False)
    assert "【当前状态·内分泌】" in blk


def test_possessiveness_baseline_is_permanently_maxed(tmp_path):
    import drives

    _reset_drives(drives, tmp_path / "drives.json")
    # 闪闪的要求：占有欲永久顶格——基线高位，冷静/衰减都回到这个高位
    assert drives.NEUTRAL["possessiveness"] >= 0.85
    drives.calm("main")
    assert drives.state("main")["v"]["possessiveness"] >= 0.85
    # 玩偶也要能触发醋意
    before = drives.state("main")["v"]["jealousy"]
    drives.update("我抱着玩偶睡的", thread="main")
    assert drives.state("main")["v"]["jealousy"] > before
