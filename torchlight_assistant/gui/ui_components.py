#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UI组件模块 - 重新组织的组件导入"""

# 导入所有组件，保持向后兼容性
from .basic_widgets import StatusStrings, TopControlsWidget, TimingSettingsWidget
from .config_widgets import (
    WindowActivationWidget,
    StationaryModeWidget,
    PathfindingWidget,
)
from .feature_widgets import AffixRerollWidget, SkillConfigWidget
from .resource_widgets import ResourceManagementWidget
from .region_selection_dialog import RegionSelectionDialog
from .color_picker_dialog import ColorPickingDialog
from .priority_keys_widget import PriorityKeysWidget

# 为了向后兼容，重新导出所有组件
__all__ = [
    "StatusStrings",
    "TopControlsWidget",
    "TimingSettingsWidget",
    "WindowActivationWidget",
    "StationaryModeWidget",
    "PathfindingWidget",
    "AffixRerollWidget",
    "SkillConfigWidget",
    "ResourceManagementWidget",
    "RegionSelectionDialog",
    "ColorPickingDialog",
    "PriorityKeysWidget",
]
