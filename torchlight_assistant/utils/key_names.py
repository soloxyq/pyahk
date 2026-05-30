"""按键名归一化工具 —— 全项目单一可信源。

内部存储、JSON 配置、Python→AHK 协议统一使用 **AHK 标准按键名**。
鼠标键必须是 LButton / RButton / MButton;GUI 可接受 right_mouse / leftclick /
mouse_right 这类别名作为输入,但归一化后统一为标准名。详见 AGENTS.md 4.5。

为什么需要这个模块:
- skills 的 Key/AltKey 此前从未经过任何归一化(只有 GUI 的 priority_keys 走过),
  导致 d4*.json 里残留 "Lbutton" 这类非标准写法;若用户手填/旧配置带入 AHK
  根本不认识的别名(如 "right_mouse"),会被原样拼成 ``press:right_mouse`` 发到
  AHK,``Send "{right_mouse down}"`` 对未知键名是静默无操作 → 技能完全不出且无报错。
- 在 MacroEngine.load_config / save_full_config 处集中归一化,即可一次性根治
  所有配置来源(预设 JSON、手填、旧配置迁移)。
"""

from typing import Any, Dict, Optional

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
    if not key:
        return ""
    normalized = str(key).lower().strip()
    return _KEY_ALIAS_MAP.get(normalized, normalized)


def normalize_key_field(value: Any) -> Any:
    """归一化一个"按键字段",兼容单键与逗号序列(含 delayN token)。

    - 单键(如 "Lbutton")→ normalize_key_name → "LButton"
    - 逗号序列(如 "delay50,Rbutton,2")→ 逐段归一化后重新拼接。delayN token 不在
      别名表里,小写返回(harmless,AHK InStr 大小写不敏感);键名/别名正常归一化。
    - 非字符串原样返回(防御性)。
    """
    if not value or not isinstance(value, str):
        return value
    if "," in value:
        parts = []
        for part in value.split(","):
            stripped = part.strip()
            parts.append(normalize_key_name(stripped) if stripped else part)
        return ",".join(parts)
    return normalize_key_name(value)


def normalize_config_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    """就地归一化一份完整配置 dict 内所有按键字段,返回同一对象。

    覆盖字段:skills.*.Key/AltKey、global.skill_sequence、
    global.priority_keys.special_keys / managed_keys(键名 + target)、
    global.stationary_mode_config 的强制移动相关键、
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

        glob = config.get("global")
        if not isinstance(glob, dict):
            return config

        # --- global.skill_sequence ---
        if isinstance(glob.get("skill_sequence"), str):
            glob["skill_sequence"] = normalize_key_field(glob["skill_sequence"])

        # --- global.priority_keys ---
        pk = glob.get("priority_keys")
        if isinstance(pk, dict):
            special = pk.get("special_keys")
            if isinstance(special, list):
                pk["special_keys"] = [normalize_key_name(k) for k in special]
            managed = pk.get("managed_keys")
            if isinstance(managed, dict):
                new_managed: Dict[str, Any] = {}
                for k, cfg in managed.items():
                    nk = normalize_key_name(k)
                    if isinstance(cfg, dict) and "target" in cfg:
                        cfg["target"] = normalize_key_name(cfg.get("target"))
                    new_managed[nk] = cfg
                pk["managed_keys"] = new_managed

        # --- global.stationary_mode_config ---
        smc = glob.get("stationary_mode_config")
        if isinstance(smc, dict):
            for field in ("hotkey", "force_move_hotkey", "force_move_replacement_key"):
                if isinstance(smc.get(field), str) and smc[field]:
                    smc[field] = normalize_key_name(smc[field])
            passthrough = smc.get("force_move_passthrough_keys")
            if isinstance(passthrough, list):
                smc["force_move_passthrough_keys"] = [
                    normalize_key_name(k) for k in passthrough
                ]

        # --- global.resource_management 的 hp/mp 按键 ---
        rm = glob.get("resource_management")
        if isinstance(rm, dict):
            for sub in ("hp_config", "mp_config"):
                node = rm.get(sub)
                if isinstance(node, dict) and isinstance(node.get("key"), str) and node["key"]:
                    node["key"] = normalize_key_name(node["key"])
    except Exception:
        # 归一化是加固层,任何异常都不应阻断配置加载
        pass
    return config
