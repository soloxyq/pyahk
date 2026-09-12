from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from ..core.event_bus import event_bus
from ..utils.debug_log import LOG_INFO

class DebugOsdWindow(QWidget):
    _show_requested = Signal()
    _hide_requested = Signal()
    _ready_requested = Signal()
    _state_update_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background:transparent;")

        # 设置固定位置（右上角）
        self.setGeometry(1200, 50, 500, 300)  # 增加宽度和高度

        self._setup_ui()
        self.current_state = {}
        # EventBus 是同步的，发布者可能是调度/OCR 工作线程。只在 handler 中
        # emit Qt signal，实际 QWidget 操作由本对象所属 GUI 线程执行。
        self._show_requested.connect(self.show)
        self._hide_requested.connect(self.hide)
        self._ready_requested.connect(self._show_ready_state)
        self._state_update_requested.connect(self._apply_state_update)
        
        self._setup_event_subscriptions()
        self.hide() # Start hidden

        # Optional: A timer to force update if events are missed (for robustness)
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(250) # Update every 250ms
        self.update_timer.timeout.connect(self._update_display_from_state)
        self.update_timer.start()

        LOG_INFO("[DebugOsdWindow] 初始化完成")

    def _setup_ui(self):
        """设置UI布局"""
        # 创建主容器 - 一个整体的透明背景
        self.main_container = QFrame()
        self.main_container.setStyleSheet("background: rgba(0, 0, 0, 160); border-radius: 10px; border: 1px solid #555;")
        
        # 设置主容器布局
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(15, 15, 15, 15)
        container_layout.setSpacing(8)
        
        # 设置窗口布局
        window_layout = QVBoxLayout()
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.addWidget(self.main_container)
        self.setLayout(window_layout)

        self.label_font = QFont("Consolas", 11)
        self.title_font = QFont("Consolas", 12, QFont.Weight.Bold)
        
        # 标题
        self.title_label = QLabel("🐛 DEBUG MODE")
        self.title_label.setFont(self.title_font)
        self.title_label.setStyleSheet("color: cyan; background: transparent; padding: 5px; border-bottom: 1px solid #555;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self.title_label)

        # 资源状态区域
        self._create_resource_section(container_layout)
        
        # 技能状态区域  
        self._create_skills_section(container_layout)
        
        # 检测区域显示区域
        self._create_detection_regions_section(container_layout)
        
        # 按键队列区域
        self._create_actions_section(container_layout)

    def _create_resource_section(self, parent_layout):
        """创建资源状态区域"""
        # 资源标题
        resource_title = QLabel("📊 资源状态")
        resource_title.setFont(self.title_font)
        resource_title.setStyleSheet("color: lightgreen; background: transparent; padding: 3px 0; border-bottom: 1px solid #444;")
        parent_layout.addWidget(resource_title)
        
        # HP/MP状态
        status_layout = QHBoxLayout()
        self.hp_label = self._create_status_label("HP: --%", "#ff6b6b")
        self.mp_label = self._create_status_label("MP: --%", "#4ecdc4")
        status_layout.addWidget(self.hp_label)
        status_layout.addWidget(self.mp_label)
        parent_layout.addLayout(status_layout)

    def _create_skills_section(self, parent_layout):
        """创建技能状态区域"""
        # 技能标题
        skills_title = QLabel("⚔️ 技能状态")
        skills_title.setFont(self.title_font)
        skills_title.setStyleSheet("color: orange; background: transparent; padding: 3px 0; border-bottom: 1px solid #444; margin-top: 5px;")
        parent_layout.addWidget(skills_title)
        
        # 技能状态容器
        self.skills_container = QLabel("等待技能数据...")
        self.skills_container.setFont(self.label_font)
        self.skills_container.setStyleSheet("color: white; background: transparent; padding: 2px;")
        self.skills_container.setWordWrap(True)
        self.skills_container.setTextFormat(Qt.TextFormat.RichText)  # 支持HTML格式
        parent_layout.addWidget(self.skills_container)

    def _create_detection_regions_section(self, parent_layout):
        """创建检测区域显示区域"""
        # 检测区域标题
        regions_title = QLabel("📋 检测区域")
        regions_title.setFont(self.title_font)
        regions_title.setStyleSheet("color: yellow; background: transparent; padding: 3px 0; border-bottom: 1px solid #444; margin-top: 5px;")
        parent_layout.addWidget(regions_title)
        
        # 检测区域容器
        self.regions_container = QLabel("等待检测区域数据...")
        self.regions_container.setFont(self.label_font)
        self.regions_container.setStyleSheet("color: white; background: transparent; padding: 2px;")
        self.regions_container.setWordWrap(True)
        self.regions_container.setTextFormat(Qt.TextFormat.RichText)
        parent_layout.addWidget(self.regions_container)

    def _create_actions_section(self, parent_layout):
        """创建按键队列区域"""
        # 按键标题
        actions_title = QLabel("⌨️ 模拟按键队列")
        actions_title.setFont(self.title_font)
        actions_title.setStyleSheet("color: yellow; background: transparent; padding: 3px 0; border-bottom: 1px solid #444; margin-top: 5px;")
        parent_layout.addWidget(actions_title)
        
        # 按键队列容器（水平滚动）
        self.actions_container = QLabel("等待按键操作...")
        self.actions_container.setFont(self.label_font)
        self.actions_container.setStyleSheet("color: white; background: transparent; padding: 2px;")
        self.actions_container.setWordWrap(False)  # 不换行，实现水平滚动效果
        parent_layout.addWidget(self.actions_container)

    def _create_status_label(self, text, color):
        """创建状态标签"""
        label = QLabel(text)
        label.setFont(self.label_font)
        label.setStyleSheet(f"color: {color}; border: none; background: transparent; padding: 2px;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    def _setup_event_subscriptions(self):
        event_bus.subscribe("debug_osd_update", self._on_debug_osd_update)
        event_bus.subscribe("debug_osd_show", self._safe_show)
        event_bus.subscribe("debug_osd_hide", self._safe_hide)
        event_bus.subscribe("debug_osd_ready_state", self._on_ready_state)
        LOG_INFO("[DebugOsdWindow] 事件订阅完成")

    def _safe_show(self):
        """线程安全的显示方法"""
        self._show_requested.emit()

    def _safe_hide(self):
        """线程安全的隐藏方法"""
        self._hide_requested.emit()

    def _on_ready_state(self):
        """处理READY状态，显示准备提示"""
        self._ready_requested.emit()

    def _show_ready_state(self):
        """显示DEBUG OSD READY状态"""
        # 重置为READY状态显示
        self.hp_label.setText("HP: READY")
        self.mp_label.setText("MP: READY") 
        self.skills_container.setText("⚡ 等待运行中...")
        self.actions_container.setText("🎯 DEBUG OSD READY - 按Z键开始")
        self.title_label.setText("🐛 DEBUG MODE - READY")
        self.title_label.setStyleSheet("color: yellow; background: transparent; padding: 5px; border-bottom: 1px solid #555;")
        LOG_INFO("[DebugOsdWindow] 显示READY状态")

    def _on_debug_osd_update(self, state_data):
        """线程安全的状态更新"""
        payload = dict(state_data or {})
        self._state_update_requested.emit(payload)
        LOG_INFO(f"[DebugOsdWindow] 收到状态更新: HP={payload.get('hp')}, MP={payload.get('mp')}, Skills={len(payload.get('skills', {}))}")

    def _apply_state_update(self, state_data):
        """GUI 线程中的不可变快照提交。"""
        self.current_state = state_data
        self._update_display_from_state()

    def _update_state_data(self, state_data):
        """在主线程中更新状态数据"""
        self.current_state = state_data
        self._update_display_from_state()
        LOG_INFO(f"[DebugOsdWindow] 状态数据已更新: HP={state_data.get('hp')}, MP={state_data.get('mp')}, Skills={len(state_data.get('skills', {}))}")

    def _update_display_from_state(self):
        """更新显示内容"""
        # 只有在有实际数据时才切换到运行状态显示
        has_data = (self.current_state.get('hp') is not None or 
                   self.current_state.get('mp') is not None or 
                   bool(self.current_state.get('skills', {})))
        
        if has_data:
            # 当有实际数据时，切换到运行状态显示
            self.title_label.setText("🐛 DEBUG MODE - RUNNING")
            self.title_label.setStyleSheet("color: cyan; background: transparent; padding: 5px; border-bottom: 1px solid #555;")
        
        # HP/MP
        hp = self.current_state.get('hp')
        mp = self.current_state.get('mp')
        self.hp_label.setText(f"HP: {hp:.0f}%" if hp is not None else "HP: --%")
        self.mp_label.setText(f"MP: {mp:.0f}%" if mp is not None else "MP: --%")

        # 技能状态
        skills = self.current_state.get('skills', {})
        if skills:
            skills_lines = []
            for skill_key, data in skills.items():
                similarity = data.get('similarity', 0)
                is_ready = data.get('is_ready', False)
                status = "✅就绪" if is_ready else "⏳冷却"
                color = "#90EE90" if is_ready else "#FFA500"
                skills_lines.append(f'<span style="color:{color};">{skill_key}: {similarity:.0f}% {status}</span>')
            skills_text = '<br>'.join(skills_lines)
            self.skills_container.setText(skills_text or "无技能数据")
        else:
            self.skills_container.setText("等待技能数据...")

        # 检测区域状态
        detection_regions = self.current_state.get('detection_regions', {})
        show_regions = self.current_state.get('show_detection_regions', True)
        if detection_regions and show_regions:
            regions_lines = []
            for region_key, data in detection_regions.items():
                region_type = data.get('type', 'unknown')
                if region_type == 'circle':
                    center_x = data.get('center_x', 0)
                    center_y = data.get('center_y', 0)
                    radius = data.get('radius', 0)
                    color = data.get('color', 'white')
                    regions_lines.append(f'<span style="color:{color};">🔵 {region_key}: ({center_x},{center_y}) r={radius}</span>')
                elif region_type == 'rectangle':
                    x1 = data.get('x1', 0)
                    y1 = data.get('y1', 0)
                    x2 = data.get('x2', 0)
                    y2 = data.get('y2', 0)
                    color = data.get('color', 'white')
                    width = x2 - x1
                    height = y2 - y1
                    regions_lines.append(f'<span style="color:{color};">⬜ {region_key}: {width}×{height} @({x1},{y1})</span>')
                
                # 添加匹配度信息（如果有）
                match_pct = data.get('match_percentage')
                if match_pct is not None:
                    regions_lines[-1] = regions_lines[-1].replace('</span>', f' [{match_pct:.1f}%]</span>')
            
            regions_text = '<br>'.join(regions_lines)
            self.regions_container.setText(regions_text or "无检测区域")
        elif not show_regions:
            self.regions_container.setText("🔒 检测区域显示已关闭")
        else:
            self.regions_container.setText("等待检测区域数据...")

        # 按键队列（从右往左水平滚动）
        actions = self.current_state.get('actions', [])
        if actions:
            # 显示最近的10个按键，从右往左排列
            display_actions = [action['text'] for action in actions[:10]]
            # 反转列表，让最新的显示在右边
            display_actions.reverse()
            actions_text = " ← ".join(display_actions)
            # 如果文本太长，添加省略号
            if len(actions_text) > 80:
                actions_text = "..." + actions_text[-77:]
            self.actions_container.setText(actions_text)
        else:
            self.actions_container.setText("等待按键操作...")

        self.adjustSize()

    def paintEvent(self, event):
        # This is needed for WA_TranslucentBackground to work correctly
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0)) # Fill with transparent color

    def closeEvent(self, event):
        self.update_timer.stop()
        event_bus.unsubscribe("debug_osd_update", self._on_debug_osd_update)
        event_bus.unsubscribe("debug_osd_show", self._safe_show)
        event_bus.unsubscribe("debug_osd_hide", self._safe_hide)
        event_bus.unsubscribe("debug_osd_ready_state", self._on_ready_state)
        LOG_INFO("[DebugOsdWindow] 关闭并取消订阅")
        super().closeEvent(event)

# Example usage (for testing, not part of main app flow)
if __name__ == '__main__':
    import sys
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from ..core.unified_scheduler import UnifiedScheduler

    app = QApplication(sys.argv)
    
    # Mock event bus and scheduler for testing
    class MockEventBus:
        def __init__(self):
            self.listeners = {}
        def subscribe(self, event_name, listener):
            if event_name not in self.listeners:
                self.listeners[event_name] = []
            self.listeners[event_name].append(listener)
        def publish(self, event_name, *args, **kwargs):
            if event_name in self.listeners:
                for listener in self.listeners[event_name]:
                    listener(*args, **kwargs)
        def unsubscribe(self, event_name, listener):
            if event_name in self.listeners and listener in self.listeners[event_name]:
                self.listeners[event_name].remove(listener)

    mock_event_bus = MockEventBus()
    mock_scheduler = UnifiedScheduler()

    # Override the global event_bus for this test
    from ..core import event_bus as global_event_bus
    global_event_bus.event_bus = mock_event_bus

    osd = DebugOsdWindow()
    osd.show()

    # Simulate updates
    test_data = {
        'hp': 95,
        'mp': 80,
        'skills': {
            'Q': {'similarity': 98, 'is_ready': True},
            'W': {'similarity': 45, 'is_ready': False},
            'E': {'similarity': 90, 'is_ready': True},
        },
        'actions': ['模拟按键: 1', '模拟按住: Shift', '模拟释放: Ctrl'],
        'action_log_max_size': 3
    }

    update_count = 0
    def simulate_update():
        global update_count
        global test_data
        update_count += 1
        test_data['hp'] = (test_data['hp'] - 5) % 101
        test_data['mp'] = (test_data['mp'] - 3) % 101
        if update_count % 5 == 0:
            test_data['actions'].insert(0, f"模拟按键: {update_count // 5}")
            if len(test_data['actions']) > test_data['action_log_max_size']:
                test_data['actions'].pop()

        mock_event_bus.publish("debug_osd_update", test_data)

        if update_count == 10:
            mock_event_bus.publish("debug_osd_hide")
        if update_count == 15:
            mock_event_bus.publish("debug_osd_show")

    timer = QTimer()
    timer.timeout.connect(simulate_update)
    timer.start(500) # Update every 500ms

    sys.exit(app.exec())
