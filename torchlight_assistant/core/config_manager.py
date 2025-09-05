"""Configuration management for Torchlight Assistant"""

import json
from pathlib import Path
from typing import Dict, Any, Optional
from ..utils.debug_log import LOG_INFO, LOG_ERROR


class ConfigManager:
    """A stateless tool for configuration file I/O."""

    def load_config(self, file_path: str) -> Dict[str, Any]:
        """
        Loads a configuration file and returns its content as a dictionary.
        Returns an empty dict on failure and logs the error.
        """
        path_to_load = Path(file_path)
        if not path_to_load.exists():
            LOG_INFO(f"WARNING: 配置文件不存在: {file_path}")
            return {}
        
        try:
            with open(path_to_load, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except json.JSONDecodeError as e:
            LOG_ERROR(f"配置文件 {file_path} 格式错误: {e}")
            return {} # Return empty dict on error
        except IOError as e:
            LOG_ERROR(f"读取配置文件 {file_path} 失败: {e}")
            return {} # Return empty dict on error

    def save_config(self, data: Dict[str, Any], file_path: str):
        """
        Saves the provided data dictionary to a JSON file.
        """
        path_to_save = Path(file_path)
        try:
            with open(path_to_save, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

        except IOError as e:

            raise
