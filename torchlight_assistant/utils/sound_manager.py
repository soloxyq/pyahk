import os
import winsound
from typing import Dict, Any

from .debug_log import LOG, LOG_INFO, LOG_ERROR

class SoundManager:
    """
    负责播放程序状态提示音的管理器 (使用内置的winsound库)。
    """
    def __init__(self, sound_dir: str = "sounds"):
        self.enabled = False
        self.sound_dir = sound_dir
        self.sounds = {
            "hello": os.path.join(self.sound_dir, "hello.wav"),
            "goodbye": os.path.join(self.sound_dir, "goodbye.wav"),
            "pause": os.path.join(self.sound_dir, "pause.wav"),
            "resume": os.path.join(self.sound_dir, "resume.wav"),
        }
        self._validate_sounds()

    def _validate_sounds(self):
        """检查所有音频文件是否存在。"""
        for name, path in self.sounds.items():
            if not os.path.exists(path):
                LOG(f"[声音管理器] WARNING: 音频文件不存在: {path}，'{name}'提示音将无法播放。")

    def update_config(self, global_config: Dict[str, Any]):
        """根据全局配置更新声音管理器的状态。"""
        self.enabled = global_config.get("sound_feedback_enabled", False)
        LOG(f"[声音管理器] 配置更新，声音提示已 {'启用' if self.enabled else '禁用'}")

    def play(self, sound_name: str):
        """
        异步播放指定名称的音效。

        Args:
            sound_name (str): 要播放的音效名 (e.g., "hello", "goodbye").
        """
        if not self.enabled:
            return

        sound_path = self.sounds.get(sound_name)
        if sound_path and os.path.exists(sound_path):
            try:
                # 使用SND_ASYNC标志进行非阻塞播放
                winsound.PlaySound(sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                LOG(f"[声音管理器] 正在播放: {sound_name}")
            except Exception as e:
                # winsound在文件格式不正确时会抛出异常
                LOG_ERROR(f"[声音管理器] 播放音频 '{sound_path}' 时出错 (可能不是有效的WAV文件): {e}")
        else:
            LOG(f"[声音管理器] WARNING: 尝试播放一个不存在或未定义的音效: {sound_name}")