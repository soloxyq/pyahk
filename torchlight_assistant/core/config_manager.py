"""Configuration management for Torchlight Assistant"""

import json
import math
import os
from pathlib import Path
from typing import Dict, Any
from ..utils.debug_log import LOG_ERROR


class ConfigManager:
    """A stateless tool for configuration file I/O."""

    @staticmethod
    def _reject_json_constant(token: str):
        """拒绝 Python json 默认容忍、但 RFC 8259 不允许的 NaN/Infinity。"""
        raise ValueError(f"JSON 包含非有限数值: {token}")

    @classmethod
    def _validate_finite_numbers(cls, value, path: str = "$") -> None:
        """连同 ``1e309`` 这类解析后溢出为 inf 的数字一起拒绝。"""
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"JSON 字段 {path} 包含非有限数值")
        if isinstance(value, dict):
            for key, child in value.items():
                cls._validate_finite_numbers(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._validate_finite_numbers(child, f"{path}[{index}]")

    def load_config(self, file_path: str) -> Dict[str, Any]:
        """加载 JSON 对象;读取、解析或顶层类型错误时记录并原样抛出。

        调用方必须能区分“合法空对象”和“加载失败”。旧实现把所有错误折叠成
        ``{}``,会让上层误把损坏文件提交成空运行配置。
        """
        path_to_load = Path(file_path)
        if not path_to_load.exists():
            error = FileNotFoundError(f"配置文件不存在: {file_path}")
            LOG_ERROR(str(error))
            raise error
        
        try:
            with open(path_to_load, "r", encoding="utf-8") as f:
                data = json.load(f, parse_constant=self._reject_json_constant)
            self._validate_finite_numbers(data)
        except (json.JSONDecodeError, ValueError) as e:
            LOG_ERROR(f"配置文件 {file_path} 格式错误: {e}")
            raise
        except OSError as e:
            LOG_ERROR(f"读取配置文件 {file_path} 失败: {e}")
            raise

        if not isinstance(data, dict):
            error = ValueError(
                f"配置文件 {file_path} 顶层必须是 JSON 对象,实际为 {type(data).__name__}"
            )
            LOG_ERROR(str(error))
            raise error

        return data

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
                json.dump(
                    data,
                    f,
                    indent=4,
                    ensure_ascii=False,
                    allow_nan=False,
                )
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
