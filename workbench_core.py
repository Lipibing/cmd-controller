from __future__ import annotations

import json
import os
import queue
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


APP_DATA_DIR_NAME = "ServiceProcessWorkbench"
CONFIG_FILE_NAME = "manager_config.json"
BACKUP_FILE_NAME = "manager_config.backup.json"

PROGRAM_DEFAULTS = {
    "name": "新服务实例",
    "service_type": "exe",
    "exe_path": "",
    "args": "",
    "jvm_args": "",
    "java_path": "java",
    "auto_start_instance": False,
}

APP_DEFAULTS = {
    "sys_auto_start": False,
    "auto_restart_on_crash": False,
    "window_geometry": "1240x820+120+80",
    "startup_delay_sec": 5,
    "startup_interval_sec": 2,
    "update_proxy": "",
}


def resolve_data_dir() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    return base / APP_DATA_DIR_NAME


def _new_service_id() -> str:
    return uuid.uuid4().hex


def _normalize_program(raw: object) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    service = {"id": str(raw.get("id") or _new_service_id())}
    for key, default in PROGRAM_DEFAULTS.items():
        value = raw.get(key, default)
        if isinstance(default, bool):
            service[key] = bool(value)
        else:
            service[key] = str(value) if value is not None else default
    if service["service_type"] not in {"exe", "jar"}:
        service["service_type"] = "exe"
    if not service["name"].strip():
        service["name"] = PROGRAM_DEFAULTS["name"]
    return service


def normalize_config_data(data: object) -> dict:
    raw = data if isinstance(data, dict) else {}
    tabs = []
    for item in raw.get("tabs", []):
        normalized = _normalize_program(item)
        if normalized is not None:
            tabs.append(normalized)
    if not tabs:
        tabs.append(_normalize_program({}))

    result = {"tabs": tabs}
    for key, default in APP_DEFAULTS.items():
        value = raw.get(key, default)
        if isinstance(default, bool):
            result[key] = bool(value)
        elif isinstance(default, int):
            try:
                result[key] = max(0, int(value))
            except (TypeError, ValueError):
                result[key] = default
        else:
            result[key] = str(value) if value else default
    return result


@dataclass(frozen=True)
class LoadResult:
    data: dict
    warning: str = ""


class ConfigStore:
    def __init__(self, data_dir: Path, legacy_paths: Iterable[Path] = ()):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / CONFIG_FILE_NAME
        self.backup_path = self.data_dir / BACKUP_FILE_NAME
        self.legacy_paths = [Path(path) for path in legacy_paths]

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_atomic(self, data: dict, destination: Optional[Path] = None) -> None:
        destination = destination or self.path
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=str(self.data_dir)
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)

    def save(self, data: dict) -> None:
        normalized = normalize_config_data(data)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                self._read(self.path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            else:
                shutil.copy2(self.path, self.backup_path)
        self._write_atomic(normalized)

    def load(self) -> LoadResult:
        if self.path.exists():
            try:
                raw = self._read(self.path)
                normalized = normalize_config_data(raw)
                if normalized != raw:
                    self.save(normalized)
                return LoadResult(normalized)
            except (OSError, ValueError, json.JSONDecodeError) as primary_error:
                if self.backup_path.exists():
                    try:
                        recovered = normalize_config_data(self._read(self.backup_path))
                        self._write_atomic(recovered)
                        return LoadResult(recovered, f"主配置损坏，已从备份恢复：{primary_error}")
                    except (OSError, ValueError, json.JSONDecodeError):
                        pass
                return LoadResult(
                    normalize_config_data({}),
                    f"配置文件无法读取，已使用默认配置：{primary_error}",
                )

        for legacy_path in self.legacy_paths:
            if not legacy_path.exists() or legacy_path.resolve() == self.path.resolve():
                continue
            try:
                migrated = normalize_config_data(self._read(legacy_path))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            self.save(migrated)
            return LoadResult(migrated, f"已导入旧版配置：{legacy_path}")

        default = normalize_config_data({})
        self.save(default)
        return LoadResult(default)


class BoundedLogQueue:
    def __init__(self, max_messages: int = 5000):
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max(1, max_messages))
        self._dropped = 0
        self._drop_lock = threading.Lock()

    @property
    def size(self) -> int:
        return self._queue.qsize()

    def put(self, message: object) -> None:
        text = str(message)
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            with self._drop_lock:
                self._dropped += 1

    def drain(self, limit: int = 1000) -> List[str]:
        messages: List[str] = []
        with self._drop_lock:
            dropped = self._dropped
            self._dropped = 0
        if dropped:
            messages.append(f"--- [系统] 日志过快，已丢弃 {dropped} 条待显示消息 ---\n")
        for _ in range(max(0, limit)):
            try:
                messages.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return messages

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._drop_lock:
            self._dropped = 0


def append_rotating_log(
    path: Path,
    text: str,
    max_bytes: int = 10 * 1024 * 1024,
    backups: int = 3,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8", errors="replace")
    max_bytes = max(1, max_bytes)
    if len(encoded) > max_bytes:
        encoded = encoded[-max_bytes:]

    current_size = path.stat().st_size if path.exists() else 0
    if current_size and current_size + len(encoded) > max_bytes:
        if backups > 0:
            oldest = Path(f"{path}.{backups}")
            oldest.unlink(missing_ok=True)
            for index in range(backups - 1, 0, -1):
                source = Path(f"{path}.{index}")
                if source.exists():
                    os.replace(source, Path(f"{path}.{index + 1}"))
            os.replace(path, Path(f"{path}.1"))
        else:
            path.unlink(missing_ok=True)

    with path.open("ab") as handle:
        handle.write(encoded)


def build_start_schedule(
    count: int,
    initial_delay_ms: int = 5000,
    interval_ms: int = 2000,
) -> List[int]:
    return [initial_delay_ms + index * interval_ms for index in range(max(0, count))]
