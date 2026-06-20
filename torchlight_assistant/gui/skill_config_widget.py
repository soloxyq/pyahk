from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QFrame,
    QPushButton,
)
from PySide6.QtCore import Qt, QTimer
from typing import Dict, Any


from .custom_widgets import (
    ConfigCheckBox,
    ConfigLineEdit,
    ConfigRadioButton,
    ConfigComboBox,
)
from ..core.event_bus import event_bus
from ..utils.debug_log import LOG_ERROR, LOG_INFO  # Import LOG_ERROR / LOG_INFO


class SimplifiedSkillWidget(QWidget):
    """UI组件, 使用自定义控件实现即时更新, 无需手动保存."""

    def __init__(
        self,
        parent,
        skill_name: str,
        initial_skill_config: Dict[str, Any],
        event_bus_instance,
    ):
        super().__init__(parent)
        self.skill_name = skill_name
        self.event_bus = event_bus_instance
        self._skills_config_snapshot = initial_skill_config.copy()

        self._ui_widgets: Dict[str, QWidget] = {}
        self._updating_ui = False

        self._create_widgets()
        self.refresh(initial_skill_config)

    def _create_widgets(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, 1)

        main_frame = QGroupBox(self.skill_name)
        main_layout.addWidget(main_frame)

        row_layout = QHBoxLayout(main_frame)
        row_layout.setContentsMargins(4, 4, 4, 4)
        row_layout.setSpacing(3)

        self._create_core_settings(row_layout)
        self._create_trigger_settings(row_layout)
        self._create_condition_settings(row_layout)

    def _create_core_settings(self, layout):
        self._ui_widgets["Enabled"] = ConfigCheckBox("启用")
        layout.addWidget(self._ui_widgets["Enabled"])

        layout.addWidget(QLabel("键值:"))
        self._ui_widgets["Key"] = ConfigLineEdit()
        self._ui_widgets["Key"].setMaximumWidth(80)  # 增加宽度以容纳序列
        self._ui_widgets["Key"].setToolTip(
            "单个按键: q, w, space, lbutton\n"
            "延迟指令: delay50, delay100\n"
            "按键序列: delay50,q 或 q,delay100,w\n"
            "（用逗号分隔，按顺序执行）"
        )
        layout.addWidget(self._ui_widgets["Key"])

        self._ui_widgets["Priority"] = ConfigCheckBox("优先级")
        layout.addWidget(self._ui_widgets["Priority"])

    def _create_trigger_settings(self, layout):
        layout.addWidget(QLabel("触发方式:"))
        self._ui_widgets["TriggerModeCombo"] = ConfigComboBox(self._on_trigger_mode_changed)
        self._ui_widgets["TriggerModeCombo"].addItems(["定时", "冷却", "按住"])
        self._ui_widgets["TriggerModeCombo"].setMaximumWidth(80)
        layout.addWidget(self._ui_widgets["TriggerModeCombo"])

        self.timer_frame = self._create_timer_frame()
        layout.addWidget(self.timer_frame)

        self.cooldown_frame = self._create_cooldown_frame()
        layout.addWidget(self.cooldown_frame)

        # 移除内部冷却框架 - 该功能未实现

    def _create_timer_frame(self):
        frame = QFrame()
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(2, 2, 2, 2)
        frame_layout.setSpacing(3)

        frame_layout.addWidget(QLabel("间隔:"))
        self._ui_widgets["Timer"] = ConfigLineEdit()
        self._ui_widgets["Timer"].setMaximumWidth(50)
        frame_layout.addWidget(self._ui_widgets["Timer"])
        frame_layout.addWidget(QLabel("ms"))

        return frame

    def _create_cooldown_frame(self):
        frame = QFrame()
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(2, 2, 2, 2)
        frame_layout.setSpacing(3)

        for prop, label, width in [
            ("CooldownCoordX", "X:", 40),
            ("CooldownCoordY", "Y:", 40),
            ("CooldownSize", "宽高:", 30),
        ]:
            frame_layout.addWidget(QLabel(label))
            self._ui_widgets[prop] = ConfigLineEdit()
            self._ui_widgets[prop].setMaximumWidth(width)
            frame_layout.addWidget(self._ui_widgets[prop])

        # 📦 框选按钮:拖拽框选该技能的冷却图标,直接填框左上角 X/Y 与方形边长(取框宽)
        select_btn = QPushButton("📦框选")
        select_btn.setMaximumWidth(60)
        select_btn.setToolTip(
            "拖拽框选该技能的冷却图标。\n"
            "直接填入框左上角 X/Y 和方形边长(冷却检测是方形,边长取框宽)。\n"
            "框成大致正方形即可。"
        )
        select_btn.clicked.connect(self._start_cooldown_region_selection)
        frame_layout.addWidget(select_btn)

        return frame

    def _start_cooldown_region_selection(self):
        """框选技能冷却图标,把框的左上角坐标与框宽直接填入 CooldownCoordX/Y/Size。

        冷却检测是方形区域(size×size, 左上角对齐),故只取框宽作为边长,不计算中心。
        """
        top_window = self.window()

        def show_dialog():
            try:
                from .region_selection_dialog import RegionSelectionDialog

                dialog = RegionSelectionDialog()

                def on_region_selected(x1, y1, x2, y2):
                    left, top = min(x1, x2), min(y1, y2)
                    w = abs(x2 - x1)
                    self._ui_widgets["CooldownCoordX"].setText(str(left))
                    self._ui_widgets["CooldownCoordY"].setText(str(top))
                    self._ui_widgets["CooldownSize"].setText(str(w))
                    LOG_INFO(
                        f"[框选] {self.skill_name} 冷却坐标已更新: "
                        f"X={left}, Y={top}, 宽高={w}"
                    )

                dialog.region_selected.connect(on_region_selected)
                dialog.exec()
            except Exception as e:
                LOG_ERROR(f"[框选] {self.skill_name} 区域选择失败: {e}")
            finally:
                # 无论确认/取消/异常,都恢复主窗口
                if top_window is not None:
                    top_window.show()
                    top_window.raise_()
                    top_window.activateWindow()

        # 先隐藏主窗口,再延迟截屏框选,避免把主界面框进截图
        if top_window is not None:
            top_window.hide()
        QTimer.singleShot(150, show_dialog)

    # _create_internal_cooldown_frame 方法已移除 - 该功能未实现

    def _create_condition_settings(self, layout):
        layout.addWidget(QLabel("条件:"))

        self._ui_widgets["ExecuteCondition"] = ConfigComboBox(
            self._on_condition_changed
        )
        self._ui_widgets["ExecuteCondition"].addItems(
            ["无限制", "BUFF限制", "资源条件"]
        )
        self._ui_widgets["ExecuteCondition"].setMaximumWidth(80)
        # 添加工具提示说明不同条件的逻辑
        self._ui_widgets["ExecuteCondition"].setToolTip(
            "无限制：直接执行主按键\n"
            "BUFF限制：检测成功时不执行，检测失败时执行主按键\n"
            "资源条件：检测成功时执行主按键，检测失败时执行额外键"
        )
        layout.addWidget(self._ui_widgets["ExecuteCondition"])

        self.condition_frame = self._create_condition_details_frame()
        layout.addWidget(self.condition_frame)

        self.alt_key_frame = self._create_alt_key_frame()
        layout.addWidget(self.alt_key_frame)

    def _create_alt_key_frame(self):
        frame = QFrame()
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(1)

        frame_layout.addWidget(QLabel("额外键:"))
        self._ui_widgets["AltKey"] = ConfigLineEdit()
        self._ui_widgets["AltKey"].setMaximumWidth(80)  # 增加宽度以容纳序列
        self._ui_widgets["AltKey"].setToolTip(
            "单个按键: q, w, space, lbutton\n"
            "延迟指令: delay50, delay100\n"
            "按键序列: delay50,q 或 q,delay100,w\n"
            "（用逗号分隔，按顺序执行）"
        )
        frame_layout.addWidget(self._ui_widgets["AltKey"])

        return frame

    def _create_condition_details_frame(self):
        frame = QFrame()
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(1)

        # 普通条件检测控件
        self.normal_condition_frame = QFrame()
        normal_layout = QHBoxLayout(self.normal_condition_frame)
        normal_layout.setContentsMargins(0, 0, 0, 0)
        normal_layout.setSpacing(1)

        for prop, label, width in [
            ("ConditionCoordX", "检测X:", 40),
            ("ConditionCoordY", "Y:", 40),
        ]:
            normal_layout.addWidget(QLabel(label))
            self._ui_widgets[prop] = ConfigLineEdit()
            self._ui_widgets[prop].setMaximumWidth(width)
            normal_layout.addWidget(self._ui_widgets[prop])

        normal_layout.addWidget(QLabel("颜色:"))
        self._ui_widgets["ConditionColor"] = ConfigLineEdit()
        self._ui_widgets["ConditionColor"].setMaximumWidth(80)
        normal_layout.addWidget(self._ui_widgets["ConditionColor"])

        note_label = QLabel("(0=智能,1=血量)")
        note_label.setStyleSheet("font-size: 8pt;")
        normal_layout.addWidget(note_label)

        frame_layout.addWidget(self.normal_condition_frame)

        # 区域资源检测控件
        self.region_condition_frame = QFrame()
        region_layout = QVBoxLayout(self.region_condition_frame)
        region_layout.setContentsMargins(0, 0, 0, 0)
        region_layout.setSpacing(2)

        # 区域资源检测功能已移至独立的"智能药剂"配置
        # 这里不再需要resource相关的配置字段
        info_label = QLabel("💡 资源检测功能已移至'智能药剂'标签页进行配置")
        info_label.setStyleSheet("color: #666; font-style: italic; padding: 10px;")
        region_layout.addWidget(info_label)

        frame_layout.addWidget(self.region_condition_frame)

        # 默认隐藏区域资源检测控件
        self.region_condition_frame.hide()

        return frame

    def get_current_config(self) -> Dict[str, Any]:
        """Synchronously gets the current configuration from the UI widgets."""
        changes = {}
        try:
            changes["Enabled"] = self._ui_widgets["Enabled"].isChecked()
            changes["Key"] = self._ui_widgets["Key"].text()
            changes["Priority"] = self._ui_widgets["Priority"].isChecked()
            changes["Timer"] = int(self._ui_widgets["Timer"].text() or 0)
            # 触发方式：0=定时, 1=冷却, 2=按住
            trigger_text = self._ui_widgets["TriggerModeCombo"].currentText()
            changes["TriggerMode"] = {"定时": 0, "冷却": 1, "按住": 2}.get(trigger_text, 0)
            changes["CooldownCoordX"] = int(
                self._ui_widgets["CooldownCoordX"].text() or 0
            )
            changes["CooldownCoordY"] = int(
                self._ui_widgets["CooldownCoordY"].text() or 0
            )
            changes["CooldownSize"] = int(self._ui_widgets["CooldownSize"].text() or 0)
            # InternalCooldown 字段已移除 - 该功能未实现
            changes["ExecuteCondition"] = {
                "无限制": 0,
                "BUFF限制": 1,
                "资源条件": 2,
            }.get(self._ui_widgets["ExecuteCondition"].currentText(), 0)
            # 普通条件检测配置
            if "ConditionCoordX" in self._ui_widgets:
                changes["ConditionCoordX"] = int(
                    self._ui_widgets["ConditionCoordX"].text() or 0
                )
            if "ConditionCoordY" in self._ui_widgets:
                changes["ConditionCoordY"] = int(
                    self._ui_widgets["ConditionCoordY"].text() or 0
                )
            if "ConditionColor" in self._ui_widgets:
                color_text = self._ui_widgets["ConditionColor"].text()
                changes["ConditionColor"] = (
                    int(color_text, 16)
                    if color_text.startswith("0x")
                    else int(color_text or 0)
                )

            # 区域资源检测配置已移至独立的"智能药剂"配置
            # 不再在skill配置中保存resource相关字段

            changes["AltKey"] = self._ui_widgets["AltKey"].text()
        except (ValueError, TypeError) as e:
            LOG_ERROR(
                f"Error gathering config for skill '{self.skill_name}': {e}"
            )  # Log the error
            return (
                self._skills_config_snapshot
            )  # Return last known good config on error
        return changes

    def refresh(self, skill_config: Dict[str, Any]):
        self._updating_ui = True
        self._skills_config_snapshot = skill_config.copy()
        try:
            config = skill_config
            if not config:
                return

            self._ui_widgets["Enabled"].setChecked(config.get("Enabled", False))
            self._ui_widgets["Key"].setText(config.get("Key", ""))
            self._ui_widgets["Priority"].setChecked(config.get("Priority", False))
            self._ui_widgets["Timer"].setText(str(config.get("Timer", 1000)))

            trigger_mode = config.get("TriggerMode", 0)
            trigger_map = {0: "定时", 1: "冷却", 2: "按住"}
            self._ui_widgets["TriggerModeCombo"].setCurrentText(trigger_map.get(trigger_mode, "定时"))

            self._ui_widgets["CooldownCoordX"].setText(
                str(config.get("CooldownCoordX", 0))
            )
            self._ui_widgets["CooldownCoordY"].setText(
                str(config.get("CooldownCoordY", 0))
            )
            self._ui_widgets["CooldownSize"].setText(
                str(config.get("CooldownSize", 12))
            )
            # InternalCooldown 字段已移除

            condition_map = {0: "无限制", 1: "BUFF限制", 2: "资源条件"}
            self._ui_widgets["ExecuteCondition"].setCurrentText(
                condition_map.get(config.get("ExecuteCondition", 0), "无限制")
            )

            # 普通条件检测配置
            if "ConditionCoordX" in self._ui_widgets:
                self._ui_widgets["ConditionCoordX"].setText(
                    str(config.get("ConditionCoordX", 0))
                )
            if "ConditionCoordY" in self._ui_widgets:
                self._ui_widgets["ConditionCoordY"].setText(
                    str(config.get("ConditionCoordY", 0))
                )
            if "ConditionColor" in self._ui_widgets:
                self._ui_widgets["ConditionColor"].setText(
                    f"0x{config.get('ConditionColor', 0):06X}"
                )

            # 区域资源检测配置已移至独立的“智能药剂”选项卡
            # 此处不再需要加载相关UI元素

            self._ui_widgets["AltKey"].setText(config.get("AltKey", ""))

            self._update_ui_visibility(config)
        finally:
            self._updating_ui = False

    def _update_ui_visibility(self, config):
        trigger_mode = config.get("TriggerMode", 0)
        is_cooldown_mode = trigger_mode == 1
        is_timer_mode = trigger_mode == 0
        is_hold_mode = trigger_mode == 2

        # 选择“按住”时隐藏定时/冷却参数
        self.cooldown_frame.setVisible(is_cooldown_mode)
        self.timer_frame.setVisible(is_timer_mode)

        condition = config.get("ExecuteCondition", 0)
        self.condition_frame.setVisible(condition != 0)

        # 普通条件检测UI（所有条件类型都使用相同的UI）
        if hasattr(self, 'normal_condition_frame'):
            self.normal_condition_frame.setVisible(True)
        if hasattr(self, 'region_condition_frame'):
            self.region_condition_frame.setVisible(False)

        # BUFF限制(1)和区域资源检测(3)没有额外键，只有资源条件(2)才显示额外键
        self.alt_key_frame.setVisible(condition == 2)

    def get_frame(self):
        return self

    def _on_trigger_mode_changed(self):
        """触发模式改变时的回调函数"""
        if self._updating_ui:
            return

        # 检查所有必要的控件是否已创建
        if not self._all_widgets_created():
            return

        # 获取当前配置并更新界面可见性
        current_config = self.get_current_config()
        self._update_ui_visibility(current_config)

    def _on_condition_changed(self):
        """执行条件改变时的回调函数"""
        if self._updating_ui:
            return

        # 检查所有必要的控件是否已创建
        if not self._all_widgets_created():
            return

        # 获取当前配置并更新界面可见性
        current_config = self.get_current_config()
        self._update_ui_visibility(current_config)

    def _all_widgets_created(self):
        """检查所有必要的控件是否已创建"""
        required_widgets = [
            "Enabled",
            "Key",
            "Priority",
            "Timer",
            "TriggerModeCombo",
            "CooldownCoordX",
            "CooldownCoordY",
            "CooldownSize",
            # "InternalCooldown" 已移除
            "ExecuteCondition",
            "AltKey",
        ]

        # 条件相关的控件（根据条件类型动态检查）
        condition_widgets = [
            "ConditionCoordX",
            "ConditionCoordY",
            "ConditionColor",
            "RegionX1",
            "RegionY1",
            "RegionX2",
            "RegionY2",
            "ResourceThreshold",
            "ColorType",
            "TargetH",
            "TargetS",
            "TargetV",
            "ToleranceH",
            "ToleranceS",
            "ToleranceV",
        ]

        # 检查基础控件
        if not all(widget_name in self._ui_widgets for widget_name in required_widgets):
            return False

        # 检查条件控件（至少要有一些）
        has_condition_widgets = any(widget_name in self._ui_widgets for widget_name in condition_widgets)
        return has_condition_widgets

    def destroy(self):
        self.deleteLater()
