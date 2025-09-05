"""
Torchlight Assistant - Python Game Automation Tool
A modular Python implementation of the AutoHotkey Torchlight assistant
"""

__version__ = "1.0.0"
__author__ = "Torchlight Assistant Team"

from .core.macro_engine import MacroEngine
from .core.skill_manager import SkillManager
from .core.config_manager import ConfigManager

__all__ = ["MacroEngine", "SkillManager", "ConfigManager"]
