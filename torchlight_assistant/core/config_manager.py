"""Configuration management for Torchlight Assistant"""

import json
import os
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

        原子写入:先写同目录临时文件并 flush+fsync 落盘,再 os.replace() 原子替换目标。
        中途失败时清理临时文件并保留原配置不被截断/损坏,然后向上抛出异常。
        """
        path_to_save = Path(file_path)
        tmp_path = path_to_save.with_name(path_to_save.name + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path_to_save)  # 同目录,原子替换(POSIX/NTFS)
        except Exception as e:
            # 不止 OSError:json.dump 遇到不可序列化对象会抛 TypeError/ValueError(循环引用等),
            # 这些同样要清理半成品 .tmp 以免残留;清理后原样向上抛出,原配置文件保持不动。
            LOG_ERROR(f"保存配置 {file_path} 失败: {e}")
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise
