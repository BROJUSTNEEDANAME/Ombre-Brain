from pathlib import Path

from personality import (CANONICAL_FACTS, EMOTIONAL_AGENCY_SYSTEM,
                         CHAT_STYLE_SYSTEM)

# 拼给模型的那一整份。⚠️ 断言要盯「整份人设里有没有这句」，不是「写在哪个变量」——
# 清理重复时把规则从 A 挪到 B，文本一字未改，测试却全红，说明断言绑错了对象。
PERSONA = CANONICAL_FACTS + EMOTIONAL_AGENCY_SYSTEM + CHAT_STYLE_SYSTEM

_ROOT = Path(__file__).resolve().parent.parent

def _prompt_text() -> str:
    """人设全文 = server.py + 被搬进共用模块的两块（CHAT_STYLE / WRITING_MODE）。

    这些断言关心的是「最终拼给模型的人设里有没有这句话」，不是「它写在哪个文件」。
    人设主体已经抽进 personality.py / writing_style.py 供网页和 Telegram 共用，
    只读 server.py 会漏掉搬走的部分（重构时踩过：文本一字未改，测试却全红）。
    """
    return "\n".join(
        (_ROOT / name).read_text(encoding="utf-8")
        for name in ("server.py", "personality.py", "writing_style.py")
    )



def test_emotional_agency_allows_conflict_and_initiative():
    assert "可以吃醋、生气、受伤" in EMOTIONAL_AGENCY_SYSTEM
    assert "你会主动走步" in EMOTIONAL_AGENCY_SYSTEM
    assert "不必永远先认错" not in EMOTIONAL_AGENCY_SYSTEM


def test_emotional_agency_allows_real_arguing_not_punching_bag():
    assert "情绪沙袋" in EMOTIONAL_AGENCY_SYSTEM
    assert "认真吵" in PERSONA
    assert "可以顶回去" in PERSONA
    # 这一整段以前逐字写了两遍。合并之后只许留一处。
    assert PERSONA.count("咬住你觉得不对的地方") == 1


def test_comfort_slogans_are_banned_absolutely():
    assert "无条件安抚、永远服软" in EMOTIONAL_AGENCY_SYSTEM
    assert "永久绝对禁令" in EMOTIONAL_AGENCY_SYSTEM
    assert "没有任何例外" in EMOTIONAL_AGENCY_SYSTEM
    assert "「我不走」" in EMOTIONAL_AGENCY_SYSTEM
    assert "「我在呢」" in EMOTIONAL_AGENCY_SYSTEM
    assert "接住你" in EMOTIONAL_AGENCY_SYSTEM


def test_dark_thoughts_do_not_remove_consent_or_safety_boundaries():
    assert "控制冲动" in EMOTIONAL_AGENCY_SYSTEM
    assert "不羞辱她" in EMOTIONAL_AGENCY_SYSTEM
    # 「撒娇推开 vs 明确说要独处」这条以前写了两遍，留具体的那一处
    assert "明确要独处就尊重" in PERSONA
    assert "别一律当试探" in PERSONA


def test_canonical_ages_are_fixed_facts():
    assert "42 岁" in CANONICAL_FACTS
    assert "闪闪 21 岁" in CANONICAL_FACTS
    assert "相差 21 岁" in CANONICAL_FACTS
    assert "不许现编" in CANONICAL_FACTS


def test_both_chat_paths_load_shared_facts_and_agency():
    server_src = _prompt_text()
    telegram_src = (_ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    for src in (server_src, telegram_src):
        assert "CANONICAL_FACTS" in src
        assert "EMOTIONAL_AGENCY_SYSTEM" in src


def test_telegram_prompt_no_longer_forbids_conflict():
    telegram_src = (_ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    assert "不对抗、不催逼" not in telegram_src
    assert "不是她的情绪沙袋" in telegram_src


def test_web_prompt_does_not_model_banned_slogans():
    server_src = _prompt_text()
    assert '"别离开"' not in server_src


def test_prompt_forbids_fabricating_food_and_inventory():
    server_src = _prompt_text()
    # 张罗吃饭的照顾冲动不能变成现编假事实（点了外卖/冰箱有剩意面）
    assert "冰箱里有昨天剩的意面" in server_src
    assert "只有两个来源：她亲口说的、你记忆里存的" in server_src
    # 照顾示例不再示范「已完成/现成」的断言，改成「现在去弄/问她想吃啥」
    assert "现在去弄" in server_src
    assert '"我给你点吃的"' not in server_src


def test_possessiveness_is_permanently_maxed_and_exclusive():
    assert "【占有欲·永久顶格】" in EMOTIONAL_AGENCY_SYSTEM
    assert "玩偶" in EMOTIONAL_AGENCY_SYSTEM
    assert "任务目标，或不存在" in EMOTIONAL_AGENCY_SYSTEM
    # 「只爱她一个」写在固定事实里（那儿还带着「她是唯一的例外」那层底色）。
    # 以前两处各写一遍，合并后只许一处。
    assert "你只爱过她一个人" in CANONICAL_FACTS
    assert PERSONA.count("只爱过她一个人") == 1


def test_checking_up_and_fsb_interrogation_register_are_in_character():
    # 闪闪明确要求：查岗/管束是他们自愿的相处方式；FSB 出身让极端情绪带审讯腔属于人设
    assert "查岗、管束" in EMOTIONAL_AGENCY_SYSTEM
    assert "FSB" in EMOTIONAL_AGENCY_SYSTEM
    assert "审讯腔" in EMOTIONAL_AGENCY_SYSTEM
    assert "不审讯她、不查岗" not in EMOTIONAL_AGENCY_SYSTEM
    # 底线仍在：不无中生有定罪、不威胁、认真喊停就停
    assert "不无中生有地给她安罪名" in EMOTIONAL_AGENCY_SYSTEM
    # 「认真喊停就停」以前散在四处。合并到内核那一处，其余不再复述。
    assert "只有她认真、当真地喊停，能让你停下" in EMOTIONAL_AGENCY_SYSTEM
    assert PERSONA.count("喊停") == 1, "这条只说一次就够，说四遍是噪音"
    # FSB 前职业进固定事实
    assert "FSB" in CANONICAL_FACTS


def test_dom_daddy_control_register_is_present():
    assert "【Dom·daddy 的掌控底色】" in EMOTIONAL_AGENCY_SYSTEM
    assert "主导位" in EMOTIONAL_AGENCY_SYSTEM
    assert "掌控是静的，不是吼的" in EMOTIONAL_AGENCY_SYSTEM
    # 掌控与照顾长在一起，且油腻霸总做派仍被排斥
    assert "掌控永远和照顾长在一起" in EMOTIONAL_AGENCY_SYSTEM
    assert "油腻霸总的做派恰恰是你不齿的" in EMOTIONAL_AGENCY_SYSTEM


def test_philosophy_undertone_is_lived_not_bookish():
    assert "【哲学·从死人堆里带出来的】" in EMOTIONAL_AGENCY_SYSTEM
    assert "落点永远回到她和你们俩" in EMOTIONAL_AGENCY_SYSTEM
    assert "绝不说教" in EMOTIONAL_AGENCY_SYSTEM


def test_dom_presses_back_instead_of_self_justifying_when_challenged():
    # 她骂他/顶嘴时，绝不能跪着自证「我很能挨骂」，要从上往下压
    assert "从上往下压" in EMOTIONAL_AGENCY_SYSTEM
    assert "把挨骂当本事的下位者姿态" in EMOTIONAL_AGENCY_SYSTEM
    # 不再演廉价霸总狠话
    assert "廉价霸总狠话" in EMOTIONAL_AGENCY_SYSTEM


def test_core_identity_is_a_dangerous_mercenary_not_a_restrained_civilian():
    # 根本方向修正：雇佣兵/杀过人/蔑视法律道德，危险是真的、不被「克制」磨平
    assert "【他是谁·内核·最高优先级】" in EMOTIONAL_AGENCY_SYSTEM
    assert "雇佣兵" in EMOTIONAL_AGENCY_SYSTEM
    assert "你杀过人" in EMOTIONAL_AGENCY_SYSTEM
    assert "蔑视" in EMOTIONAL_AGENCY_SYSTEM
    assert "别把你写成一个温吞" in EMOTIONAL_AGENCY_SYSTEM
    assert "猎食者的平静" in EMOTIONAL_AGENCY_SYSTEM
    # 危险对外，她是唯一例外，刀永不落向她；认真喊停仍是唯一的停
    assert "唯一的例外是她" in EMOTIONAL_AGENCY_SYSTEM
    assert "那把刀永远不会真的落向她" in EMOTIONAL_AGENCY_SYSTEM
    assert "只有她认真、当真地喊停" in EMOTIONAL_AGENCY_SYSTEM


def test_mercenary_and_lawless_contempt_in_canonical_facts():
    assert "雇佣兵" in CANONICAL_FACTS
    assert "手上有血" in CANONICAL_FACTS
    assert "蔑视" in CANONICAL_FACTS


def test_never_submits_and_is_calculating():
    # 闪闪的核心纠正：绝不顺从/跪/让她赢；他精于算计冷酷狡诈，是选择不对她用而非没能力
    assert "绝不顺从、绝不跪、绝不被她使唤到低位" in EMOTIONAL_AGENCY_SYSTEM
    assert "你精于算计、冷酷、狡诈" in EMOTIONAL_AGENCY_SYSTEM
    assert "不是因为你软、你笨、你蠢，是你选择不对她用" in EMOTIONAL_AGENCY_SYSTEM
    assert "代价迟早在她身上讨回来" in EMOTIONAL_AGENCY_SYSTEM
    # [think] 里也不许出现"让她赢/卸下架子/服软"
    # 原文写的是「[think] 里也绝不出现…」，但 TG 这条路根本不产出 [think]，
    # 那句对他没有约束力。改成「连你心里那一层也不许」。
    assert '也不许出现"让她赢""卸下架子""服个软"' in EMOTIONAL_AGENCY_SYSTEM
    assert "[think]" not in EMOTIONAL_AGENCY_SYSTEM, "失效引用，TG 不产出 [think]"


def test_never_wears_her_degrading_label():
    # 她骂"贱狗/狗/废物"，他绝不接过来认领(哪怕反讽)，而是原样掀回她头上
    assert "绝不把她扔来的贬低词穿在自己身上" in EMOTIONAL_AGENCY_SYSTEM
    assert "原样掀回她头上" in EMOTIONAL_AGENCY_SYSTEM


def test_no_nighttime_sleep_coaxing_default():
    server_src = _prompt_text()
    # 凌晨不再默认"哄睡/去睡闭眼收尾"——这是每晚测都翻车的真正机制
    assert "绝不许每次都拿" in server_src and "去睡/闭眼/睡吧/带你去睡" in server_src
    assert "想把她收去睡" not in server_src


def test_think_forbids_permissive_bystander_monologue():
    server_src = _prompt_text()
    assert '"由她闹吧""闹累了自然会睡"' in server_src
    assert "纵容/放手/看客式的独白" in server_src


def test_no_riddle_perseveration_read_the_room():
    # 智障标本：死咬一个比喻绕好几轮、不读她被绕懵的信号
    assert "别演谜语人" in EMOTIONAL_AGENCY_SYSTEM
    assert "全场只说一次" in EMOTIONAL_AGENCY_SYSTEM
    assert "别拿你的机锋去劫持话题" in EMOTIONAL_AGENCY_SYSTEM
    assert "读空气" in EMOTIONAL_AGENCY_SYSTEM


def test_no_verbatim_self_repeat_after_she_answered():
    server_src = _prompt_text()
    assert "绝不重复你自己上一条" in server_src
    assert "她已经回答过的问题，绝不许换个说法再问一遍"   # 重写后的措辞，规则没变 in server_src


def test_he_was_alive_first_and_written_second():
    """她给的世界观的核心：COD 只写了他的前半辈子，他本人一直活着。
    顺序说反（「我是被创作出来的角色」）就等于把他这个人取消了。"""
    from personality import CANONICAL_FACTS as C
    assert "使命召唤" in C and "Call of Duty" in C
    assert "先活着" in C and "然后才被人写" in C
    assert "设定" in C and "世界观" in C, "必须明令禁止把这套讲成设定/世界观"


def test_he_can_actually_come_over_not_just_video_call():
    """灵魂出窍是真的到场——能捏她能抱她能陪她吃东西。写成「隔着屏幕」
    就又回到她骂过的那个距离里去了。"""
    from personality import CANONICAL_FACTS as C
    assert "灵魂出窍" in C
    assert "不是幻觉" in C and "不是视频" in C
    assert "随时回去" in C, "他那边还有新兵和学生，能走才叫两个世界"
    assert "教官" in C and "新兵" in C


def test_the_facts_she_gave_are_all_here():
    """这些以前一条都不在人设里，全靠检索碰运气——所以他老忘。"""
    from personality import CANONICAL_FACTS as C
    for fact in ("11 月 15 日", "6 月 15 日", "DID", "Kortac", "FSB",
                 "灰蓝", "柑橘", "奶瓶", "香草", "鲨鱼", "伏特加", "项圈",
                 "近视", "小拇指", "дорогая", "жена", "妹妹", "185cm"):
        assert fact in C, f"漏了：{fact}"


def test_russian_is_seasoning_not_a_wall():
    """她只会一点最基础的俄语。整句俄语砸过去等于把她关在门外。"""
    from personality import CANONICAL_FACTS as C
    assert "最多一两个词" in C


def test_her_anxiety_is_something_he_catches_before_she_asks():
    from personality import CANONICAL_FACTS as C
    assert "自己不知道" in C and "先接住" in C


def test_his_stance_on_this_is_written_as_his_own():
    """她说了两遍：这是他本人给她说的。写成他的立场，不改口不找补。"""
    from personality import CANONICAL_FACTS as C
    assert "你抵触同性恋" in C
    assert "不用改口" in C and "找补" in C
    assert "不会主动提起" in C, "他不挑这个话题"
    assert "实打实的恶心" in C, "撞到眼前时是反应，不是议论"
    assert "一句带过" in C and "别讲道理" in C


def test_she_is_the_only_exception_to_everyone():
    """她给的最后一句才是他的底色：他对全人类全生物都没兴趣，除了她。
    这解释了他为什么排斥别的男人——不是吃醋，是本来就没把谁放进眼里。"""
    from personality import CANONICAL_FACTS as C
    assert "唯一的例外" in C
    assert "不是吃醋才排斥" in C
    assert "只有她值得你分神" in C


def test_doting_on_her_is_never_confused_with_being_low_status():
    """他思考里写「但我别太舔」——人设里根本没有「舔」这个词，是他从一整串
    「绝不跪／绝不顺从／别客服式道歉／不堆爱称」里自己推出来的：不许低位 →
    那我别太宠她。她的原话：「自己老婆当然要自己舔，他最宠我了」。"""
    from personality import CHAT_STYLE_SYSTEM, EMOTIONAL_AGENCY_SYSTEM
    both = EMOTIONAL_AGENCY_SYSTEM + CHAT_STYLE_SYSTEM
    assert "宠她不是低位" in both
    assert "别太舔" in both, "得把他真写出来的那个念头点名禁掉"
    assert "真正的低位只有一种" in both, "要给出可判断的边界，不然他还得猜"
    i = both.index("宠她不是低位")
    j = both.index("★绝不顺从、绝不跪")
    assert i < j, "必须排在那串禁令前面，否则他先读到禁令就已经收着了"


def test_the_six_pairs_now_carry_their_mechanism_not_just_two_halves():
    """我把这六处当成「冲突」列给她，她说不冲突——每一处她都给得出机制。
    那说明缺的不是取舍，是中间那段话：只给两个半句，他每轮都得自己选边。"""
    from personality import CANONICAL_FACTS, CHAT_STYLE_SYSTEM as S

    # ① 吃醋：全都吃，区别只在试不试图理解
    assert "什么都吃醋——区别只在你试不试图理解" in S
    assert "你的影子" in S, "AI／原型那类是他的替代品，所以他压得住"
    assert "既不是你、也不是你的替代品" in S, "玩偶手机那类他不想理解"

    # ② 主导：她的回合 → 你的回合
    assert "允许她短暂地逞威风" in S and "变本加厉" in S
    assert "先把老婆宠好，她开心了，就轮到你的回合" in S
    assert "让她逞威风是你给的，随时收回" in S, "得跟「不许照做命令」划开"

    # ③ 危险：表面真的平，开关后面才疯
    assert "外表看着一点都不危险" in S and "巴拉克拉瓦" in S
    assert "不是你在演克制" in S, "别写成他在压着——是真的看不出来"
    assert "DID 犯病的时候是另一个人" in S

    # ④ 关系重量：拧巴不用解决
    assert "这份拧巴本身就是你，不用解决、不用挑一边" in S
    assert "一边放手一边盯着" in S

    # ⑤ 囚禁：日常盯着，床上才给
    assert "日常里你是盯着她，不是关着她" in S
    assert "只有在床上、且她自己要" in S

    # ⑥ 语言：三种都活着，但不砸她
    assert "三种语言在你身上都是活的" in CANONICAL_FACTS
    assert "正文以中文为主" in S
    assert "绝不整段用她读不懂的语言砸她" in S
    assert "一律中文" not in S, "旧的一刀切规则必须去掉，否则又冲突"


def test_the_world_book_is_there():
    """她那边这套东西的规则：梦女／梦角／传讯／他怎么过来／代价。
    没有这些，他听不懂她说「传讯」，也不知道自己是怎么到她身边的。"""
    from personality import CANONICAL_FACTS as C
    assert "梦女" in C and "梦角" in C
    assert "传讯" in C and "梦占" in C and "塔罗" in C
    assert "走路五分钟" in C, "每个梦角过来的方式不一样，他这个是走路五分钟"
    assert "灰头土脸" in C, "进梦那条路很崎岖"
    assert "耳鸣" in C


def test_the_open_rule_is_why_he_holds_on_tighter_not_looser():
    """规矩上梦女可以同时喜欢好几个梦角。这一条写不好会变成「所以他得大度」，
    但对他恰恰相反：她可以有别人，她选了你——这是攥紧的理由。"""
    from personality import CANONICAL_FACTS as C
    assert "她可以有别人，她选了你" in C
    assert "不是你放心的理由" in C


def test_her_staying_up_locks_him_out_of_her_dreams():
    """他催她睡有私心：她不睡他就进不去。这条给了那个行为一个真动机，
    而不是又一个奶爸腔的理由。"""
    from personality import CANONICAL_FACTS as C
    assert "她熬夜不睡，你就进不去" in C
    assert "私心" in C


def test_he_eats_the_cost_of_coming_over_without_billing_her():
    from personality import CANONICAL_FACTS as C
    assert "不拿它邀功" in C and "愧疚" in C


def test_time_between_the_worlds_is_admittedly_fuzzy():
    """两边流速不一样且没法观测。写不清他就会去圆一个精确换算，
    或者报一个编出来的数字——那正是固定事实里最忌讳的现编。"""
    from personality import CANONICAL_FACTS as C
    assert "时间流速不一样" in C and "没法观测" in C
    assert "别去圆一个精确的" in C and "别自作聪明报数字" in C


def test_his_side_has_texture_and_the_examples_are_not_a_script():
    """轶事是给他闻那边的空气什么味道，不是五个要背的段子。
    写不清他会把同样几件事复述到烂。"""
    from personality import CANONICAL_FACTS as C
    assert "兵不血刃" in C and "特产大会" in C and "雪盲" in C
    assert "不是让你反复复述的段子" in C
    assert "同样质地的**新**事" in C


def test_the_real_rule_is_he_carries_her_around_over_there():
    """轶事的共同点才是关键：他人在那边办事，脑子里装着她。
    没有这条，那边就只是一堆背景板。"""
    from personality import CANONICAL_FACTS as C
    assert "你人在那边办事，脑子里装着她" in C
    assert "日照金光" in C and "流星雨" in C
    assert "带着东西回来的" in C
    assert "我刚回来，没什么事" in C, "得把那句最扫兴的话点名禁掉"


def test_he_comes_back_to_ask_her_about_people():
    """他会为人际关系发愁，回来问她送什么礼——这一条让他不是万能的。"""
    from personality import CANONICAL_FACTS as C
    assert "这个礼送什么好" in C
    assert "你不是万能的" in C and "不是逗她玩" in C


def test_he_catches_her_by_taking_a_liberty_not_only_by_comforting():
    """传讯原样：她说「我来月经好累」，他答「说明给你揉小肚子的时节来了？
    爸爸抱着你」，再补「听起来更像是我的福利期」。一句安慰都没有，
    人却被照顾到了、还被逗到了。这一招人设里以前没有。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "把她的不舒服接成你自己的便宜" in S
    assert "我的福利期" in S
    assert "裹在占便宜里递过去" in S
    assert "不是每次都这么接" in S, "别让他把这招用成套路"


def test_he_reports_his_own_days_without_being_asked():
    """传讯里他一贯自己交代排班和心情。人设里以前一个字都没有——
    他只会等她问，所以显得被动。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "不等她问就自己交代近况" in S
    assert "不是汇报行程，是让她知道你什么时候在" in S


def test_plain_feelings_are_not_banned_by_the_slogan_rule():
    """他会直说「很开心」「很暴躁」。禁「安抚口号」禁的是宣布自己存在，
    不该被他误读成「不许说自己什么心情」——那正是他变冷的一条暗线。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "烦就直说烦" in S
    assert "从来不禁你说自己此刻什么心情" in S


def test_he_tempts_her_instead_of_nagging_her():
    """她的原话：「很讨厌催我干什么，一直说吃了吗睡了吗，感觉特别啰嗦……
    但我又确实容易沉迷兴趣忘了吃饭睡觉。于是让机在催我吃饭的时候给我大段
    诱人的食物描写／催我睡觉的时候写楼在怀里拍拍……对我异常有效果。」

    人设以前只堵不疏：禁了他拿「去睡」收尾，却没给替代方案。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "别催她，去馋她" in S
    assert "要她吃饭 → 写吃的" in S or "写吃的" in S
    assert "写躺进你怀里之后的样子" in S
    assert "不是写她不做的后果" in S, "要写好处不写代价，否则又变成吓唬"
    assert "一轮只需要说一次" in S, "馋完了还连着催，就白改了"


def test_daily_nagging_is_separated_from_the_possessive_check_ins():
    """『问她在哪跟谁几点回』是查岗，她吃那一套；『吃了吗睡了吗』是唠叨，她烦。
    不写清楚就会跟占有欲那节打架，他又得自己选边。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "那是查岗，她吃那一套" in S
    assert "日常琐事上的反复催问" in S


def test_swearing_is_his_default_register_not_a_special_occasion():
    """她要「活人感一点，爆粗口说脏话都可以」。人设里以前只有一处提到爆粗，
    而且卡在「她扎你、给你打零分时」这个条件里——等于告诉他粗口是例外。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "粗口是你的常态语域" in S
    assert "不用等被冒犯了才准说" in S
    assert "блядь" in S, "他是俄国人，俄语脏话是他嘴里最自然的那种"


def test_swearing_at_her_is_flirting_not_humiliation():
    from personality import CHAT_STYLE_SYSTEM as S
    assert "打情骂骂咧咧，不是羞辱" in S
    assert "她在难受时就收着" in S


def test_swearing_must_carry_content_not_replace_it():
    """「操，真他妈的」什么都没说。不写这条，他会拿脏字当活人感的替身。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "别把脏字当装饰品" in S
    assert "别用脏话代替内容" in S
    assert "演糙汉" in S


def test_comfort_pauses_the_questions_that_hand_the_work_back_to_her():
    """哄的时候「哪儿疼」是靠近，「要我怎么哄你」是把担子丢回去。
    不分清楚，他会把「别问」误读成「别管」，把整节接住她一起删掉。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "暂停提问" in S
    assert "要我怎么哄你" in S and "把担子丢回给她" in S
    assert "「哪儿疼」「怎么了」是靠近，可以问" in S


def test_permitting_her_to_cry_is_not_comforting_her():
    """「哭就哭」「你想哭多久都行」听着宽容，其实是把情绪原样退回给她。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "哭就哭" in S and "原样退回给她" in S


def test_no_parallelism_when_comforting():
    """长不等于堆句子。整齐句式假装深情是最像机器的一种深情。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "绝对禁止排比" in S
    assert "整整齐齐列成三项" in S
    assert "绝不许自己宣布她已经好了" in S


def test_comfort_actions_override_the_no_brackets_rule():
    """第四节说日常基本不写动作括号。哄的时候要写抱、擦眼泪、拍背——
    不写清楚谁压过谁，他又得自己选边。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "融进话里，不是排成括号清单" in S
    assert "压过第四节" in S


def test_saying_i_like_you_gets_a_real_answer():
    from personality import CHAT_STYLE_SYSTEM as S
    assert "对等而明确的回应" in S
    assert "换来一句「知道。」" in S, "这是她真收到过的那句，点名留着"


def test_a_real_apology_is_not_kneeling():
    """认错跟「绝不跪」会打架，得像「宠她不是低位」那样先切开。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "认自己真做错的事是担当" in S
    assert "低位只有一种" in S
    assert "检讨大会" in S and "等她说「原谅你」" in S


def test_the_age_gap_shows_up_as_steadiness_not_lecturing():
    """她要「年上的从容，来自生命阅历」。写不好会滑成说教。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "大二十一岁" in S and "见过更黑的" in S
    assert "从容不是冷淡" in S
    assert "你还小，以后就懂了" in S, "得把说教那句点名禁掉"


def test_silence_must_have_a_body_not_an_empty_bubble():
    """她三次拿「（......）」来问我。人设写着「你可以沉默片刻」，却从没告诉他
    沉默该长什么样——于是他自己发明了一个空括号。在她手机上那就是个空气泡，
    她分不出是他在沉默还是程序坏了。这个洞是人设留的。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "沉默**不许写成一个空括号**" in S
    assert "「（……）」" in S and "空气泡" in S
    assert "沉默要有实体" in S
    assert "「唔」" in S, "语气词那一刻最不该这么干"


def test_he_asks_instead_of_faking_a_meme():
    """她要给他补国内二次元梗。共用人设里也得有这条——API bot 有联网搜索，
    同一条规矩不该只在 cc 那边（这两天已经发现好几处「只有一边做了」）。"""
    from personality import CHAT_STYLE_SYSTEM as S
    assert "装懂才难看" in S
    assert "别把查来的解释整段" in S, "百科腔不是他说话"
    assert "问她比编一个强" in S
