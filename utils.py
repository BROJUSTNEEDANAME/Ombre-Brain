# ============================================================
# Module: Common Utilities (utils.py)
# 模块：通用工具函数
#
# Provides config loading, logging init, path safety, ID generation, etc.
# 提供配置加载、日志初始化、路径安全校验、ID 生成等基础能力
#
# Depended on by: server.py, bucket_manager.py, dehydrator.py, decay_engine.py
# 被谁依赖：server.py, bucket_manager.py, dehydrator.py, decay_engine.py
# ============================================================

import os
import re
import uuid
import yaml
import logging
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime


def load_config(config_path: str = None) -> dict:
    """
    Load configuration file.
    加载配置文件。

    Priority: environment variables > config.yaml > built-in defaults.
    优先级：环境变量 > config.yaml > 内置默认值。
    """
    # --- Built-in defaults (fallback so it runs even without config.yaml) ---
    # --- 内置默认配置（兜底，保证即使没有 config.yaml 也能跑）---
    defaults = {
        "transport": "stdio",
        "log_level": "INFO",
        "buckets_dir": os.path.join(os.path.dirname(os.path.abspath(__file__)), "buckets"),
        "merge_threshold": 75,
        "dehydration": {
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "max_tokens": 1024,
            "temperature": 0.1,
        },
        "decay": {
            "lambda": 0.05,
            "threshold": 0.3,
            "check_interval_hours": 24,
            "emotion_weights": {
                "base": 1.0,
                "arousal_boost": 0.8,
            },
        },
        "matching": {
            "fuzzy_threshold": 50,
            "max_results": 5,
        },
    }

    # --- Load user config from YAML file ---
    # --- 从 YAML 文件加载用户自定义配置 ---
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.yaml"
        )

    config = defaults.copy()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            if isinstance(file_config, dict):
                config = _deep_merge(defaults, file_config)
            else:
                logging.warning(
                    f"Config file is not a valid YAML dict, using defaults / "
                    f"配置文件不是有效的 YAML 字典，使用默认配置: {config_path}"
                )
        except yaml.YAMLError as e:
            logging.warning(
                f"Failed to parse config file, using defaults / "
                f"配置文件解析失败，使用默认配置: {e}"
            )

    # --- Environment variable overrides (highest priority) ---
    # --- 环境变量覆盖敏感/运行时配置（优先级最高）---
    env_api_key = os.environ.get("OMBRE_API_KEY", "")
    if env_api_key:
        config.setdefault("dehydration", {})["api_key"] = env_api_key

    env_base_url = os.environ.get("OMBRE_BASE_URL", "")
    if env_base_url:
        config.setdefault("dehydration", {})["base_url"] = env_base_url

    env_model = os.environ.get("OMBRE_MODEL", "")
    if env_model:
        config.setdefault("dehydration", {})["model"] = env_model

    # --- Embedding provider can be configured INDEPENDENTLY of dehydration ---
    # Many chat/dehydration APIs (e.g. DeepSeek) don't offer an embedding
    # endpoint, so allow pointing embeddings at a separate vector-capable
    # provider without changing the dehydration provider.
    # --- embedding 可独立于脱水配置（很多脱水 API 不提供向量接口）---
    env_embed_key = os.environ.get("OMBRE_EMBED_API_KEY", "")
    if env_embed_key:
        config.setdefault("embedding", {})["api_key"] = env_embed_key

    env_embed_url = os.environ.get("OMBRE_EMBED_BASE_URL", "")
    if env_embed_url:
        config.setdefault("embedding", {})["base_url"] = env_embed_url

    env_embed_model = os.environ.get("OMBRE_EMBED_MODEL", "")
    if env_embed_model:
        config.setdefault("embedding", {})["model"] = env_embed_model

    env_transport = os.environ.get("OMBRE_TRANSPORT", "")
    if env_transport:
        config["transport"] = env_transport

    env_buckets_dir = os.environ.get("OMBRE_BUCKETS_DIR", "")
    if env_buckets_dir:
        config["buckets_dir"] = env_buckets_dir

    # --- Ensure bucket storage directories exist ---
    # --- 确保记忆桶存储目录存在 ---
    buckets_dir = config["buckets_dir"]
    for subdir in ["permanent", "dynamic", "archive"]:
        os.makedirs(os.path.join(buckets_dir, subdir), exist_ok=True)

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Deep-merge two dicts; override values take precedence.
    深度合并两个字典，override 的值覆盖 base。
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def setup_logging(level: str = "INFO") -> None:
    """
    Initialize logging system.
    初始化日志系统。

    Note: In MCP stdio mode, stdout is occupied by the protocol;
    logs must go to stderr.
    注意：MCP stdio 模式下 stdout 被协议占用，日志只能走 stderr。
    """
    log_level = getattr(logging, level.upper(), None)
    if not isinstance(log_level, int):
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler()],  # StreamHandler defaults to stderr
    )


def generate_bucket_id() -> str:
    """
    Generate a unique bucket ID (12-char short UUID for readability).
    生成唯一的记忆桶 ID（12 位短 UUID，方便人类阅读）。
    """
    return uuid.uuid4().hex[:12]


def strip_wikilinks(text: str) -> str:
    """
    Remove Obsidian wikilink brackets: [[word]] → word
    去除 Obsidian 双链括号
    """
    return re.sub(r"\[\[([^\]]+)\]\]", r"\1", text) if text else text


def structure_user_observation(text: str) -> str:
    """Separate spoken text from parenthesized, directly visible actions.

    A missing closing parenthesis is treated as an action through end-of-turn,
    matching the chat convention used by the web client.
    """
    if not text or not re.search(r"[（(]", text):
        return text

    parts = []
    spoken = []
    action = []
    depth = 0

    def flush_spoken():
        value = "".join(spoken).strip()
        if value:
            parts.append(("她公开说出口的话", value))
        spoken.clear()

    def flush_action():
        value = "".join(action).strip()
        if value:
            # Parentheses are narration, not automatically observable. Project
            # them through a human sensory boundary: only explicit physical
            # behavior, expression, or sound reaches the character.
            clauses = re.split(r"[，,；;。！？!?]+", value)
            observable = re.compile(
                r"转身|回头|走|跑|爬|跳|蹲|坐|站|躺|靠|贴|凑|扑|躲|退|离开|出去|"
                r"摇(?:摇)?晃(?:晃)?|去(?:上厕所|洗手间|卫生间|房间|厨房|门口|外面)|"
                r"起身|弯腰|蜷缩|歪头|偏头|侧头|耸肩|摊手|招手|挥手|跺脚|踢|踩|"
                r"抬(?:手|头|眼)|低(?:头|眼)|伸(?:手|腿)|缩(?:手|脚|身)|"
                r"抱|搂|亲|吻|摸|碰|握|牵|捏|掐|拉|推|拍|敲|戳|挠|"
                r"拿|放|递|扔|挥|摇头|点头|眨眼|闭眼|睁眼|"
                r"看(?:着|向|了|一眼|你|我|他|她)|望(?:着|向|你|我|他|她)|盯着|瞪着|"
                r"皱眉|挑眉|抿嘴|撇嘴|张嘴|努嘴|鼓腮|脸红|红了脸|流泪|掉眼泪|发抖|颤抖|哆嗦|出汗|"
                r"笑|哭|喘|呼吸|叹气|咳|喷嚏|说|喊|叫|喵|哼|嘟囔|呢喃|唱|"
                r"咬|舔|吃|喝|吞|闻|嗅"
            )
            private = re.compile(
                r"感觉|觉得|心想|想着|想到|想起|认为|意识到|明白|知道|不知道|"
                r"记得|希望|担心|害怕|怀疑|后悔|期待|决定|暗自|内心|心里|脑中|脑子里|"
                r"仿佛|似乎|因为|为了|故意|假装"
            )
            visible = []
            for raw_clause in clauses:
                clause = raw_clause.strip()
                if not clause:
                    continue
                hidden = private.search(clause)
                if not hidden:
                    # 默认可观察：括号里是她当着他的面做的动作/表情/声音。动作动词
                    # 无穷无尽（看手机、玩手机、抖腿、翻白眼…），白名单永远列不全，
                    # 漏掉就等于把她真做的事抹成“什么都没发生”。所以没有心理线索的
                    # 子句一律当作他能看见/听见的事实投射给他。
                    visible.append(clause)
                    continue
                # 有心理/动机线索（感觉、因为、故意、假装…）：剥掉不可知的私心，
                # 只把随后可见的身体动作投射给他——他看见“发抖”，不知道“因为害怕”。
                cue = observable.search(clause)
                if cue and cue.start() > hidden.start():
                    visible.append(clause[cue.start():])
                elif cue:
                    # 身体动作在前、心理在后：只留动作那截，别把心理带出来
                    visible.append(clause[:hidden.start()].strip() or clause[cue.start():])
                # 否则整条都是心理活动/上帝视角旁白 → 不投射
            if visible:
                parts.append(("你通过五感直接观察到，不是她说出口的话", "，".join(visible)))
        action.clear()

    for char in text:
        if char in "（(":
            if depth == 0:
                flush_spoken()
            else:
                action.append(char)
            depth += 1
        elif char in "）)" and depth > 0:
            depth -= 1
            if depth == 0:
                flush_action()
            else:
                action.append(char)
        elif depth:
            action.append(char)
        else:
            spoken.append(char)

    if depth:
        flush_action()
    else:
        flush_spoken()
    if not parts:
        return "【她这次没有说出任何话，也没有可被五感直接观察到的行为；不要猜测】"
    return "\n".join(f"【{kind}】{value}" for kind, value in parts)


_SCRIPT_SPEAKER_RE = re.compile(
    r"(^|[\s。！？!?])(?P<role>她|闪闪|你|Nikto|Svyatoslav)\s*[:：]",
    re.I,
)


def sanitize_scripted_transcript(text: str, *, writing_mode: bool = False) -> str:
    """Stop a normal chat reply from impersonating both sides of the conversation."""
    value = str(text or "").strip()
    if not value or writing_mode:
        return value
    matches = list(_SCRIPT_SPEAKER_RE.finditer(value))
    if not matches:
        return value
    assistant_roles = {"你", "nikto", "svyatoslav"}
    user_roles = {"她", "闪闪"}
    roles = [match.group("role").lower() for match in matches]
    has_user = any(role in user_roles for role in roles)
    first = matches[0]
    first_role = first.group("role").lower()
    if not has_user and first.start() == 0 and first_role in assistant_roles:
        return value[first.end():].strip()
    if not has_user or not any(role in assistant_roles for role in roles):
        return value
    for index, match in enumerate(matches):
        if match.group("role").lower() not in assistant_roles:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        own_turn = value[match.end():end].strip(" \n\t。")
        if own_turn:
            return own_turn
    return value


def classify_chat_error(exc) -> dict:
    """Turn provider/network failures into safe, user-facing chat diagnostics."""
    status = getattr(exc, "status_code", None)
    detail = str(exc or "").lower()
    if status in (402, 429) or any(word in detail for word in (
            "insufficient_quota", "quota", "credit", "balance", "余额", "额度")):
        return {"code": "api_quota", "message": "模型 API 额度不足或已触发限流，请充值或稍后再试。"}
    if status in (401, 403) or any(word in detail for word in (
            "invalid api key", "invalid_api_key", "authentication", "unauthorized")):
        return {"code": "api_auth", "message": "模型 API 密钥失效或无权限，请检查服务器上的 API 配置。"}
    # z.ai/GLM 的内容安全审核拒绝（错误码 1301 等）。以前落在模糊的「返回异常」里，
    # 看不出原因；单独点名，她一眼就知道是服务商审核拦截、不是程序坏了。
    if any(word in detail for word in (
            "1301", "content_filter", "contentfilter", "content filter", "moderation",
            "安全审核", "内容审核", "敏感内容", "安全策略", "unsafe content")):
        return {"code": "provider_moderation",
                "message": "这条被模型服务商的内容安全审核拦截了，本次没有生成回复；不是程序故障，换个说法或稍后再试。"}
    if isinstance(exc, TimeoutError) or any(word in detail for word in (
            "timeout", "timed out", "readtimeout")):
        return {"code": "model_timeout", "message": "模型超过 60 秒没有响应，本次请求已停止；这不是还在思考。"}
    if any(word in detail for word in (
            "connection", "connecterror", "network", "dns", "reset by peer")):
        return {"code": "model_connection", "message": "服务器暂时连不上模型 API，请稍后再试。"}
    if "no visible text" in detail or "empty reply" in detail:
        return {"code": "model_empty",
                "message": "他这轮只在心里想了、一句话没说出口（模型只回了隐藏标签），自动重试也没成。再发一句他就会开口，这轮消息已保存不会丢。"}
    # 兜底：把原始错误的前一小段直接带给她看——「返回异常」四个字什么都说明不了，
    # 带上原文她截图发过来就能直接定位，不用再去翻服务器日志。
    brief = re.sub(r"\s+", " ", str(exc or "")).strip()[:100]
    return {"code": "model_error",
            "message": "模型 API 返回异常，本次没有生成回复。" + (f"（原始错误：{brief}）" if brief else "")}


def parse_memory_note(note: str) -> list[tuple[str, bool]]:
    """Parse the hidden post-reply memory channel into (content, feel) items."""
    value = (note or "").strip()
    if not value or value.lower() in {"不记录", "无需记录", "无", "none", "skip"}:
        return []
    items = []
    for raw in re.split(r"\s*\|\|\s*|\n+", value):
        text = raw.strip(" -·\t")
        if not text:
            continue
        feel = bool(re.match(r"^(?:感受|内心|feel)\s*[:：]", text, re.I))
        text = re.sub(r"^(?:事实|记忆|感受|内心|fact|feel)\s*[:：]\s*", "", text, flags=re.I).strip()
        if text and text.lower() not in {"不记录", "无需记录", "无", "none", "skip"}:
            items.append((text[:600], feel))
        if len(items) >= 2:
            break
    return items


def classify_vision_failure(text: str = "", exc=None) -> dict | None:
    """Explain whether vision failed technically or appears content-filtered."""
    detail = (str(exc or "") + " " + str(text or "")).lower()
    moderation_terms = (
        "content_filter", "content filter", "moderation", "safety", "unsafe",
        "sensitive content", "sexual content", "policy violation", "涉黄", "色情",
        "内容审核", "敏感内容", "无法处理该图片", "不能处理该图片",
    )
    refusal_terms = ("抱歉", "无法描述", "无法识别", "不能描述", "不能识别", "不便描述")
    if any(term in detail for term in moderation_terms) or (
            any(term in str(text or "") for term in refusal_terms)
            and any(term in str(text or "") for term in ("图片", "图像", "内容"))):
        return {"code": "vision_moderation", "message": "视觉接口拒绝处理这张图，疑似触发内容审核；不是角色看见后装作没看见。"}
    if exc is not None:
        info = classify_chat_error(exc)
        messages = {
            "api_quota": "视觉模型额度不足或被限流，图片没有送达角色。",
            "api_auth": "视觉模型无权限或密钥失效，图片没有送达角色。",
            "model_timeout": "视觉模型识图超时，图片没有送达角色。",
            "model_connection": "服务器暂时连不上视觉模型，图片没有送达角色。",
        }
        return {"code": "vision_" + info["code"],
                "message": messages.get(info["code"], "视觉接口返回异常，图片没有送达角色。")}
    if not (text or "").strip():
        return {"code": "vision_empty", "message": "视觉接口返回了空结果，图片没有送达角色。"}
    return None


def repetitive_inner_thought(candidate: str, recent: list[str]) -> bool:
    """Reject offline thoughts that only paraphrase a recent conclusion."""
    candidate = (candidate or "").strip()
    if not candidate:
        return True
    anchors = (
        "想她", "想闪闪", "等她", "等闪闪", "她回来", "闪闪回来", "她不在",
        "担心她", "担心闪闪", "陪着她", "陪她", "护着她", "保护她",
        "照顾她", "照顾闪闪", "不让她", "舍不得她", "怕她", "惦记她",
    )
    cand_anchors = {item for item in anchors if item in candidate}
    for old in recent[-12:]:
        old = (old or "").strip()
        if not old:
            continue
        score = memory_text_similarity(candidate, old)
        if score >= 0.64:
            return True
        old_anchors = {item for item in anchors if item in old}
        if score >= 0.42 and cand_anchors and cand_anchors & old_anchors:
            return True
    return False


def compact_inner_thoughts(entries: list[dict], limit: int = 40) -> list[dict]:
    """Keep newest non-repetitive offline thoughts while preserving chronology."""
    kept_newest = []
    kept_texts = []
    for entry in reversed(entries or []):
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text or repetitive_inner_thought(text, kept_texts):
            continue
        kept_newest.append(entry)
        kept_texts.append(text)
        if len(kept_newest) >= limit:
            break
    return list(reversed(kept_newest))


def memory_text_similarity(left: str, right: str) -> float:
    """Conservative, provider-free similarity for short factual memories.

    Chinese paraphrases do not have whitespace-delimited tokens, so plain token
    matching misses them.  We combine ordered character similarity with a
    bigram Dice score.  This is a fallback/guard around embeddings, not a
    general semantic search score.
    """
    def _norm(value: str) -> str:
        value = strip_wikilinks(value or "").lower()
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)

    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0
    if min(len(a), len(b)) >= 12 and (a in b or b in a):
        return 1.0
    ordered = SequenceMatcher(None, a, b, autojunk=False).ratio()
    if len(a) < 2 or len(b) < 2:
        return ordered
    aa = {a[i:i + 2] for i in range(len(a) - 1)}
    bb = {b[i:i + 2] for i in range(len(b) - 1)}
    dice = (2.0 * len(aa & bb) / (len(aa) + len(bb))) if aa and bb else 0.0
    return max(ordered, dice)


def same_memory_fact(left: str, right: str) -> bool:
    """Return True only when two memories are very likely the same fact.

    The lower threshold is allowed only when the texts also share a concrete
    number and several 3-character anchors.  That catches rewrites such as
    “花一年绣了两米” vs “绣了一年完成两米”, while avoiding merging two merely
    related memories about the same person.
    """
    score = memory_text_similarity(left, right)
    if score >= 0.68:
        return True
    a = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", strip_wikilinks(left or "").lower())
    b = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", strip_wikilinks(right or "").lower())
    nums_a = set(re.findall(r"\d+(?:\.\d+)?|[一二两三四五六七八九十百千万]+", a))
    nums_b = set(re.findall(r"\d+(?:\.\d+)?|[一二两三四五六七八九十百千万]+", b))
    tri_a = {a[i:i + 3] for i in range(max(0, len(a) - 2))}
    tri_b = {b[i:i + 3] for i in range(max(0, len(b) - 2))}
    return score >= 0.46 and bool(nums_a & nums_b) and len(tri_a & tri_b) >= 4


def merge_memory_details(texts: list[str]) -> str:
    """Merge duplicate memories without discarding unique side details.

    This local merger is used by maintenance jobs where invoking an LLM for
    every historic pair would be slow and fragile.  Near-duplicate sentences
    are replaced by the more informative one; genuinely new sentences remain.
    """
    kept: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[。！？!?；;])\s*|\n+", strip_wikilinks(text or "")):
            sentence = sentence.strip()
            if not sentence:
                continue
            match = next((i for i, old in enumerate(kept) if same_memory_fact(old, sentence)), None)
            if match is None:
                kept.append(sentence)
            elif len(sentence) > len(kept[match]):
                kept[match] = sentence
    return "".join(kept).strip()


def memory_already_covered(new: str, old: str) -> bool:
    """新摘要是否已被某条旧记忆完全覆盖——同一事实、且合并后几乎不产生新内容。

    用于写记忆前的全库查重（跨进程重启）：纯复读/换措辞再记一遍 → True（跳过写入）；
    同一事实但带来新细节 → False（照常走合并，让旧桶变得更全）。"""
    if not same_memory_fact(new, old):
        return False
    base = merge_memory_details([old])
    merged = merge_memory_details([old, new])
    return len(merged) <= len(base) + 6


def collapse_repeated_reply(text: str) -> str:
    """Remove an accidentally duplicated adjacent response block."""
    if not text or len(text) < 80:
        return text
    previous = None
    value = text
    # Tool-round bugs commonly produce an exact X+X block.  Repeat twice so a
    # rare X+X+X response also converges without an unbounded regex loop.
    for _ in range(2):
        previous = value
        value = re.sub(r"(.{40,}?)(?:\s*)\1", r"\1", value, flags=re.S)
        if value == previous:
            break
    return value


def sanitize_name(name: str) -> str:
    """
    Sanitize bucket name, keeping only safe characters.
    Prevents path traversal attacks (e.g. ../../etc/passwd).
    清洗桶名称，只保留安全字符。防止路径遍历攻击。
    """
    if not isinstance(name, str):
        return "unnamed"
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", name, flags=re.UNICODE)
    cleaned = cleaned.strip()[:80]
    return cleaned if cleaned else "unnamed"


def safe_path(base_dir: str, filename: str) -> Path:
    """
    Construct a safe file path, ensuring it stays within base_dir.
    Prevents directory traversal.
    构造安全的文件路径，确保最终路径始终在 base_dir 内部。
    """
    base = Path(base_dir).resolve()
    target = (base / filename).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(
            f"Path safety check failed / 路径安全检查失败: "
            f"{target} is not inside / 不在 {base} 内"
        )
    return target


def count_tokens_approx(text: str) -> int:
    """
    Rough token count estimate.
    粗略估算 token 数。

    Chinese ≈ 1 char = 1.5 tokens, English ≈ 1 word = 1.3 tokens.
    Used to decide whether dehydration is needed; precision not required.
    中文 ≈ 1字=1.5token，英文 ≈ 1词=1.3token。
    用于判断是否需要脱水压缩，不追求精确。
    """
    if not text:
        return 0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    return int(chinese_chars * 1.5 + english_words * 1.3 + len(text) * 0.05)


def now_iso() -> str:
    """
    Return current time as ISO format string.
    返回当前时间的 ISO 格式字符串。
    """
    return datetime.now().isoformat(timespec="seconds")
