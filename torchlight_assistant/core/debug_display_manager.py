import time
from .event_bus import EventBus
from ..utils.debug_log import LOG_INFO, LOG_ERROR

class DebugDisplayManager:
    def __init__(self, event_bus: EventBus, unified_scheduler):
        self.event_bus = event_bus
        self.unified_scheduler = unified_scheduler
        self.state = {
            'hp': None,
            'mp': None,
            'skills': {},
            'actions': [],
            'detection_regions': {}  # 新增：检测区域信息
        }
        self.action_log_max_size = 10  # 增加到10个按键
        self.is_active = False  # 添加激活状态标志
        self.show_detection_regions = True  # 是否显示检测区域
        
        # Define the update interval for the OSD
        self.interval_ms = 200 # Update every 200ms
        self.task_name = "debug_osd_update_task"
        
        LOG_INFO(f"[DebugDisplayManager] 初始化完成，调度任务间隔: {self.interval_ms}ms")

    def start(self):
        """启动debug数据发布"""
        if not self.is_active:
            self.is_active = True
            # 先启动调度器
            if not self.unified_scheduler.get_status()["running"]:
                self.unified_scheduler.start()
            # 然后添加调度任务
            self.unified_scheduler.add_task(self.task_name, self.interval_ms / 1000.0, self.publish_state, start_immediately=True)
            LOG_INFO("[DebugDisplayManager] Debug数据发布已启动")

    def stop(self):
        """停止debug数据发布"""
        if self.is_active:
            self.is_active = False
            # 移除调度任务
            self.unified_scheduler.remove_task(self.task_name)
            # 如果没有其他任务，停止调度器
            status = self.unified_scheduler.get_status()
            task_count = status.get("task_count", 0)  # 安全地获取task_count
            if task_count == 0:
                self.unified_scheduler.stop()
            LOG_INFO("[DebugDisplayManager] Debug数据发布已停止")

    def update_health(self, hp_percent):
        self.state['hp'] = hp_percent
        if self.is_active:  # 只有在调试模式激活时才打印
            LOG_INFO(f"[DebugDisplayManager] HP更新: {hp_percent}%")

    def update_mana(self, mp_percent):
        self.state['mp'] = mp_percent
        if self.is_active:  # 只有在调试模式激活时才打印
            LOG_INFO(f"[DebugDisplayManager] MP更新: {mp_percent}%")

    def update_skill_status(self, skill_key, similarity, is_ready):
        if 'skills' not in self.state:
            self.state['skills'] = {}
        self.state['skills'][skill_key] = {
            'similarity': similarity,
            'is_ready': is_ready,
            'timestamp': time.time()
        }
        if self.is_active:  # 只有在调试模式激活时才打印
            LOG_INFO(f"[DebugDisplayManager] 技能状态更新: {skill_key} - 相似度:{similarity:.1f}%, 就绪:{is_ready}")

    def add_action(self, action_text):
        if 'actions' not in self.state:
            self.state['actions'] = []
        
        self.state['actions'].insert(0, {
            'text': action_text,
            'timestamp': time.time()
        })
        
        # Keep the log size fixed
        if len(self.state['actions']) > self.action_log_max_size:
            self.state['actions'].pop()
        LOG_INFO(f"[DebugDisplayManager] 动作添加: {action_text}")

    def update_detection_region(self, region_type: str, region_info: dict):
        """更新检测区域信息
        
        Args:
            region_type: 区域类型，如 'hp_circle', 'mp_rectangle', 'skill_1' 等
            region_info: 区域信息，包含坐标、颜色等
        """
        if 'detection_regions' not in self.state:
            self.state['detection_regions'] = {}
        
        self.state['detection_regions'][region_type] = {
            **region_info,
            'timestamp': time.time()
        }
        
        if self.is_active:
            LOG_INFO(f"[DebugDisplayManager] 检测区域更新: {region_type} - {region_info}")

    def toggle_detection_regions(self):
        """切换检测区域显示"""
        self.show_detection_regions = not self.show_detection_regions
        LOG_INFO(f"[DebugDisplayManager] 检测区域显示: {'开启' if self.show_detection_regions else '关闭'}")
        return self.show_detection_regions

    def publish_state(self):
        """
        This method is called by the scheduler at a throttled rate
        and pushes the entire state to the UI.
        """
        # 只有在激活状态时才发布
        if not self.is_active:
            return
            
        # 添加详细的状态日志
        skills_count = len(self.state.get('skills', {}))
        actions_count = len(self.state.get('actions', []))
        regions_count = len(self.state.get('detection_regions', {}))
        LOG_INFO(f"[DebugDisplayManager] 发布状态: HP={self.state.get('hp')}, MP={self.state.get('mp')}, Skills={skills_count}, Actions={actions_count}, Regions={regions_count}")
        
        # 添加检测区域显示标志
        publish_state = self.state.copy()
        publish_state['show_detection_regions'] = self.show_detection_regions
        
        self.event_bus.publish('debug_osd_update', publish_state)

    def get_state(self):
        return self.state