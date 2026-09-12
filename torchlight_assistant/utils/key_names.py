"""按键名归一化工具 —— 全项目单一可信源。

内部存储、JSON 配置、Python→AHK 协议统一使用 **AHK 标准按键名**。
鼠标键必须是 LButton / RButton / MButton / XButton1 / XButton2;GUI 可接受 right_mouse / leftclick /
mouse_right 这类别名作为输入,但归一化后统一为标准名。详见 AGENTS.md 4.5。

为什么需要这个模块:
- skills 的 Key/AltKey 此前从未经过任何归一化(只有 GUI 的 priority_keys 走过),
  导致 d4*.json 里残留 "Lbutton" 这类非标准写法;若用户手填/旧配置带入 AHK
  根本不认识的别名(如 "right_mouse"),会被原样拼成 ``press:right_mouse`` 发到
  AHK,``Send "{right_mouse down}"`` 对未知键名是静默无操作 → 技能完全不出且无报错。
- 在 MacroEngine.load_config / save_full_config 处集中归一化,即可一次性根治
  所有配置来源(预设 JSON、手填、旧配置迁移)。
"""

from typing import Any, Dict, List, Optional

try:
    from .config_values import config_int
except ImportError:  # 支持测试/迁移脚本按文件路径直接加载本模块
    from torchlight_assistant.utils.config_values import config_int

# 别名(统一小写) → AHK 标准名。与历史 GUI 实现保持一致。
_KEY_ALIAS_MAP = {
    # 鼠标键
    "left_mouse": "LButton",
    "leftmouse": "LButton",
    "mouse_left": "LButton",
    "lbutton": "LButton",
    "leftclick": "LButton",
    "right_mouse": "RButton",
    "rightmouse": "RButton",
    "mouse_right": "RButton",
    "rbutton": "RButton",
    "rightclick": "RButton",
    "middle_mouse": "MButton",
    "middlemouse": "MButton",
    "mouse_middle": "MButton",
    "mbutton": "MButton",
    "xbutton1": "XButton1",
    "xbutton2": "XButton2",
    "x1": "XButton1",
    "x2": "XButton2",
    "button8": "XButton1",
    "button9": "XButton2",
    # 特殊键
    "spacebar": "space",
    "space_bar": "space",
    "control": "ctrl",
    "return": "enter",
    "escape": "esc",
}


def normalize_key_name(key: Optional[str]) -> str:
    """单个按键名归一化为 AHK 标准名。

    未命中别名表的键名仅做小写+去空格返回(AHK 的 Send/Hotkey 键名大小写不敏感,
    故普通键小写是安全且统一的)。空值返回空字符串。
    """
    if not isinstance(key, str) or not key:
        return ""
    normalized = key.lower().strip()
    return _KEY_ALIAS_MAP.get(normalized, normalized)


def normalize_key_field(value: Any) -> Any:
    """归一化一个"按键字段",兼容单键与逗号序列(含 delayN token)。

    - 单键(如 "Lbutton")→ normalize_key_name → "LButton"
    - 逗号序列(如 "delay50,Rbutton,2")→ 逐段归一化后重新拼接。delayN token 不在
      别名表里,小写返回(harmless,AHK InStr 大小写不敏感);键名/别名正常归一化。
    - 非字符串返回空串。JSON 数字/布尔不是合法按键名，不能被悄悄转成 ``1``/``true``。
    """
    if not isinstance(value, str) or not value:
        return ""
    if "," in value:
        parts = []
        for part in value.split(","):
            stripped = part.strip()
            parts.append(normalize_key_name(stripped) if stripped else part)
        return ",".join(parts)
    return normalize_key_name(value)


# 通用宏(雷蛇式)步骤类型。down/up/press 需要 key;delay 需要 ms。
_MACRO_STEP_TYPES = ("down", "up", "press", "delay")


def normalize_macro_steps(steps: Any) -> List[Dict[str, Any]]:
    """归一化一份宏步骤列表:键名标准化、ms 转非负 int、丢弃非法步骤。

    任何非 dict / 未知 type / 缺 key 的 down/up/press / ms 不可解析的 delay 都被跳过,
    保证返回的列表里每一项都是干净可执行的步骤。非列表输入返回空列表。
    """
    result: List[Dict[str, Any]] = []
    if not isinstance(steps, list):
        return result
    for s in steps:
        if not isinstance(s, dict):
            continue
        stype = s.get("type")
        if stype not in _MACRO_STEP_TYPES:
            continue
        if stype == "delay":
            try:
                ms = config_int(s.get("ms", 0))
            except (TypeError, ValueError):
                continue
            result.append({"type": "delay", "ms": max(ms, 0)})
        else:  # down / up / press
            k = normalize_key_name(s.get("key"))
            if not k:
                continue
            result.append({"type": stype, "key": k})
    return result


def migrate_skill_sequence_to_steps(seq: Any) -> List[Dict[str, Any]]:
    """旧 CSV 序列 → 通用宏步骤列表(向后兼容)。

    与 AHK 序列 token 解析 / 旧 delayN 约定保持一致:仅 ``delay<数字>`` 识别为延时步骤,
    其余(含畸形 delay token)按普通键转成 press 步骤。
    """
    steps: List[Dict[str, Any]] = []
    if not isinstance(seq, str):
        return steps
    for token in seq.split(","):
        t = token.strip()
        if not t:
            continue
        low = t.lower()
        if low.startswith("delay") and low[5:].strip().isdigit():
            steps.append({"type": "delay", "ms": int(low[5:].strip())})
        else:
            steps.append({"type": "press", "key": normalize_key_name(t)})
    return steps


def steps_to_legacy_sequence(steps: Any) -> str:
    """宏步骤列表 → 旧 CSV(降级/可读性)。

    仅当所有步骤都是 press/delay(旧 CSV 能无损表达)时才生成字符串;一旦含 down/up
    这类旧格式无法表达的步骤,返回 "" —— 避免写出语义错误的旧序列(macro_steps 才是权威)。
    """
    if not isinstance(steps, list):
        return ""
    tokens: List[str] = []
    for s in steps:
        if not isinstance(s, dict):
            return ""
        stype = s.get("type")
        if stype == "press":
            k = s.get("key")
            if not k:
                return ""
            tokens.append(str(k))
        elif stype == "delay":
            try:
                tokens.append("delay" + str(config_int(s.get("ms", 0))))
            except (TypeError, ValueError):
                return ""
        else:
            return ""  # down/up 无法用旧 CSV 表达
    return ",".join(tokens)


def normalize_config_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    """就地归一化一份完整配置 dict 内所有按键字段,返回同一对象。

    覆盖字段:skills.*.Key/AltKey、global.skill_sequence、global.macro_steps
    (含旧 skill_sequence → macro_steps 的一次性迁移)、
    global.priority_keys.special_keys / managed_keys(键名 + target)、
    global.stationary_mode_config 的强制移动相关键、global.boss_mode_hotkey、
    global.resource_management 的 hp/mp 按键。

    任何字段缺失或结构异常都安全跳过(整体 try/except 包裹),绝不因归一化导致
    配置加载失败。
    """
    if not isinstance(config, dict):
        return config
    try:
        # --- skills.*.Key / AltKey ---
        skills = config.get("skills")
        if isinstance(skills, dict):
            for skill in skills.values():
                if not isinstance(skill, dict):
                    continue
                if "Key" in skill:
                    skill["Key"] = normalize_key_field(skill["Key"])
                if "AltKey" in skill:
                    skill["AltKey"] = normalize_key_field(skill["AltKey"])
                if "BossOnly" in skill:
                    # JSON 布尔字段只接受真正的 true。``"false"`` 在 Python
                    # 中 truthy，旧写法会把它反向归一成 True。
                    value = skill["BossOnly"]
                    skill["BossOnly"] = value is True
                    try:
                        trigger_mode = config_int(skill.get("TriggerMode", 0))
                    except (TypeError, ValueError):
                        trigger_mode = 0
                    if trigger_mode == 2:
                        skill["BossOnly"] = False

        glob = config.get("global")
        if not isinstance(glob, dict):
            return config

        # --- global.skill_sequence ---
        if isinstance(glob.get("skill_sequence"), str):
            glob["skill_sequence"] = normalize_key_field(glob["skill_sequence"])

        if "boss_mode_hotkey" in glob:
            glob["boss_mode_hotkey"] = normalize_key_name(
                glob.get("boss_mode_hotkey")
            )

        # --- global.macro_steps (通用宏步骤) ---
        # 迁移:仅当 macro_steps 键缺失 且 skill_sequence 非空时,从旧 CSV 合成。
        # 注意只看"键是否存在",尊重用户显式清空成 [](不再从旧序列回填)。
        if "macro_steps" not in glob:
            seq = glob.get("skill_sequence")
            if isinstance(seq, str) and seq.strip():
                glob["macro_steps"] = migrate_skill_sequence_to_steps(seq)
        # 归一化已有/刚迁移的步骤(键名标准化、ms 转 int、过滤非法项)
        if isinstance(glob.get("macro_steps"), list):
            glob["macro_steps"] = normalize_macro_steps(glob["macro_steps"])

        # --- global.priority_keys ---
        pk = glob.get("priority_keys")
        if isinstance(pk, dict):
            special = pk.get("special_keys")
            if isinstance(special, list):
                pk["special_keys"] = [
                    normalized
                    for key in special
                    if (normalized := normalize_key_name(key))
                ]
            managed = pk.get("managed_keys")
            if isinstance(managed, dict):
                new_managed: Dict[str, Any] = {}
                for k, cfg in managed.items():
                    nk = normalize_key_name(k)
                    if not nk:
                        continue
                    if isinstance(cfg, dict) and "target" in cfg:
                        cfg["target"] = normalize_key_name(cfg.get("target"))
                    new_managed[nk] = cfg
                pk["managed_keys"] = new_managed

        # --- global.stationary_mode_config ---
        smc = glob.get("stationary_mode_config")
        if isinstance(smc, dict):
            for field in ("hotkey", "force_move_hotkey", "force_move_replacement_key"):
                if field in smc:
                    smc[field] = normalize_key_name(smc.get(field))
            passthrough = smc.get("force_move_passthrough_keys")
            if isinstance(passthrough, list):
                smc["force_move_passthrough_keys"] = [
                    normalize_key_name(k)
                    for k in passthrough
                    if isinstance(k, str) and k.strip()
                ]

        # --- global.resource_management 的 hp/mp 按键 ---
        rm = glob.get("resource_management")
        if isinstance(rm, dict):
            for sub in ("hp_config", "mp_config"):
                node = rm.get(sub)
                if isinstance(node, dict) and "key" in node:
                    node["key"] = normalize_key_name(node.get("key"))
    except Exception:
        # 归一化是加固层,任何异常都不应阻断配置加载
        pass
    return config
