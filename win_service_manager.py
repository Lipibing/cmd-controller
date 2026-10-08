#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 服务进程管理工作台 (Lark-like UI)
"""

from __future__ import annotations
import hashlib
import queue
import getpass
import tempfile
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import customtkinter as ctk
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional
from workbench_update import latest_release, download_release, launch_replacement

from workbench_core import (
    BoundedLogQueue,
    ConfigStore,
    append_rotating_log,
    build_start_schedule,
    resolve_data_dir,
)

# --- 核心逻辑适配 ---
try:
    from jar_service_manager import JarServiceConfig, build_jar_command
except ImportError:
    @dataclass
    class JarServiceConfig:
        jar_path: str
        args: str
        jvm_args: str
        java_path: str


    def build_jar_command(cfg: JarServiceConfig):
        cmd = [cfg.java_path]
        if cfg.jvm_args:
            cmd.extend(shlex.split(cfg.jvm_args))
        cmd.extend(["-jar", cfg.jar_path])
        if cfg.args:
            cmd.extend(shlex.split(cfg.args))
        return cmd, Path(cfg.jar_path).parent

# --- 颜色与样式配置 ---
COLOR_APP_BG = "#FFFFFF"
COLOR_SIDEBAR_BG = "#F7F7F8"
COLOR_CARD_BG = "#FFFFFF"
COLOR_PRIMARY = "#242529"
COLOR_PRIMARY_HOVER = "#3B3C40"
COLOR_TEXT_TITLE = "#242529"
COLOR_TEXT_BODY = "#52535A"
COLOR_TEXT_MUTED = "#73747C"
COLOR_BORDER = "#E7E7EB"
COLOR_INPUT_BG = "#FFFFFF"
COLOR_BTN_SECONDARY = "#F1F1F3"
COLOR_BTN_SECONDARY_HOVER = "#E7E7EB"
COLOR_SUCCESS = "#2BA471"  # 运行绿
COLOR_SUCCESS_BG = "#E8F7F0"
COLOR_STOP = "#98A2B3"  # 停止灰
COLOR_STOP_BG = "#F2F4F7"
COLOR_DANGER = "#B42332"  # 停止红
COLOR_LOG_BG = "#17181C"
COLOR_LOG_TEXT = "#E0E0E5"
COLOR_SEARCH_HIGHLIGHT = "#FF9D00"
COLOR_SEARCH_CURRENT = "#3370FF"
COLOR_WARNING = "#B45309"
COLOR_WARNING_BG = "#FFF7ED"
COLOR_ERROR_BG = "#FEF2F2"

FONT_FAMILY = "Microsoft YaHei UI"
APP_VERSION = "v2.10"
CREATE_NO_WINDOW = 0x08000000
IS_FROZEN = getattr(sys, "frozen", False)
APP_SCRIPT_PATH = Path(sys.executable).resolve() if IS_FROZEN else Path(__file__).resolve()
BASE_DIR = APP_SCRIPT_PATH.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DATA_DIR = resolve_data_dir()
CONFIG_PATH = DATA_DIR / "manager_config.json"
LOG_DIR = DATA_DIR / "logs"
MAX_UI_LOG_LINES = 5000
MAX_UI_LOG_CHARS = 500_000
LOG_UI_BATCH_SIZE = 500
MAX_DISK_LOG_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3

JAR_JVM_TEMPLATES = {
    "轻量(512M)": "-Xms256m -Xmx512m -Dfile.encoding=UTF-8",
    "标准(2G,G1)": "-Xms1g -Xmx2g -XX:+UseG1GC -Dfile.encoding=UTF-8",
    "大内存(4G,G1)": "-Xms2g -Xmx4g -XX:+UseG1GC -Dfile.encoding=UTF-8",
    "调试模板": "-Xms256m -Xmx512m -Dfile.encoding=UTF-8 -Dspring.profiles.active=dev",
}


# --- 注册表开机自启管理 ---
class AutoStartManager:
    REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    APP_KEY = "WinServiceManager_Lpbing"

    @staticmethod
    def expected_command():
        if IS_FROZEN:
            return f'"{APP_SCRIPT_PATH}"'
        py_exec = Path(sys.executable)
        pyw_exec = py_exec.with_name('pythonw.exe')
        return f'"{pyw_exec if pyw_exec.exists() else py_exec}" "{APP_SCRIPT_PATH}"'

    @staticmethod
    def inspect():
        if os.name != 'nt':
            return False, '仅支持 Windows'
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AutoStartManager.REG_PATH) as key:
                command, _ = winreg.QueryValueEx(key, AutoStartManager.APP_KEY)
        except FileNotFoundError:
            return False, '未设置登录自启'
        return command == AutoStartManager.expected_command(), command

    @staticmethod
    def set_status(enabled: bool) -> None:
        if os.name != "nt": return
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AutoStartManager.REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AutoStartManager.APP_KEY, 0, winreg.REG_SZ, AutoStartManager.expected_command())
            else:
                try:
                    winreg.DeleteValue(key, AutoStartManager.APP_KEY)
                except FileNotFoundError:
                    pass
        valid, command = AutoStartManager.inspect()
        if enabled and not valid:
            raise RuntimeError(f'自启写入后校验失败: {command}')


# --- UI 组件辅助 ---
def apply_hover(widget: tk.Widget, normal_bg: str, hover_bg: str):
    widget.bind("<Enter>", lambda _: widget.configure(bg=hover_bg) if str(widget.cget("state")) != "disabled" else None)
    widget.bind("<Leave>",
                lambda _: widget.configure(bg=normal_bg) if str(widget.cget("state")) != "disabled" else None)


class WorkbenchButton(ctk.CTkButton):
    def config(self, **kwargs):
        if 'bg' in kwargs:
            kwargs['fg_color'] = kwargs.pop('bg')
        changed = {key: value for key, value in kwargs.items() if self.cget(key) != value}
        if changed:
            self.configure(**changed)


class WorkbenchEntry(ctk.CTkEntry):
    config = ctk.CTkEntry.configure


class WorkbenchBadge(ctk.CTkLabel):
    def config(self, **kwargs):
        if 'bg' in kwargs:
            kwargs['fg_color'] = kwargs.pop('bg')
        if 'fg' in kwargs:
            kwargs['text_color'] = kwargs.pop('fg')
        changed = {key: value for key, value in kwargs.items() if self.cget(key) != value}
        if changed:
            self.configure(**changed)


ctk.set_appearance_mode('light')
ctk.set_widget_scaling(1.0)


def make_button(parent: tk.Widget, text: str, command: Callable, *, primary=False, danger=False, ghost=False,
                px=20, py=8, font_size=9):
    bg = COLOR_PRIMARY if primary else ("#FDECEC" if danger else (COLOR_SIDEBAR_BG if ghost else COLOR_BTN_SECONDARY))
    fg = "#FFFFFF" if primary else (COLOR_DANGER if danger else COLOR_TEXT_BODY)
    hover = COLOR_PRIMARY_HOVER if primary else ("#FAD5D2" if danger else ("#F2F3F5" if ghost else COLOR_BTN_SECONDARY_HOVER))
    font = tkfont.Font(family=FONT_FAMILY, size=font_size, weight='bold' if primary else 'normal')
    btn = WorkbenchButton(parent, text=text, command=command,
                    width=font.measure(text) + px * 2, height=max(30, font.metrics('linespace') + py * 2),
                    fg_color=bg, text_color=fg, hover_color=hover,
                    text_color_disabled='#A0A0A8', corner_radius=8,
                    font=(FONT_FAMILY, round(font_size * 4 / 3), 'bold' if primary else 'normal'))
    btn.bind('<Button-1>', lambda _: btn.focus_set(), add='+')
    btn.bind('<Return>', lambda _: btn.invoke(), add='+')
    btn.bind('<space>', lambda _: btn.invoke(), add='+')
    btn.bind('<FocusIn>', lambda _: btn.configure(border_width=1, border_color='#8B8C94'), add='+')
    btn.bind('<FocusOut>', lambda _: btn.configure(border_width=0), add='+')
    return btn


def make_entry(parent: tk.Widget, var: tk.StringVar):
    entry = WorkbenchEntry(parent, textvariable=var, fg_color=COLOR_INPUT_BG,
                    text_color=COLOR_TEXT_TITLE, border_color=COLOR_BORDER, border_width=1,
                    height=36, corner_radius=6, font=(FONT_FAMILY, 13))
    entry.bind('<FocusIn>', lambda _: entry.configure(border_color='#8B8C94'), add='+')
    entry.bind('<FocusOut>', lambda _: entry.configure(border_color=COLOR_BORDER), add='+')
    return entry


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget, self.text, self.tip_window = widget, text, None
        self.widget.bind("<Enter>", self.show_tip);
        self.widget.bind("<Leave>", self.hide_tip)
        self.widget.bind("<Destroy>", self.hide_tip, add="+")

    def show_tip(self, _):
        if self.tip_window or not self.text: return
        tw = tk.Toplevel(self.widget);
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{self.widget.winfo_rootx() + 18}+{self.widget.winfo_rooty() + 30}")
        tk.Label(tw, text=self.text, bg="#1F2329", fg="#FFFFFF", font=(FONT_FAMILY, 9), padx=10, pady=6).pack()
        self.tip_window = tw

    def hide_tip(self, _=None):
        if self.tip_window: self.tip_window.destroy(); self.tip_window = None


# --- 核心逻辑类 ---
@dataclass
class ProgramConfig:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "新服务实例"
    service_type: str = "exe"
    exe_path: str = ""
    args: str = ""
    jvm_args: str = ""
    java_path: str = "java"
    auto_start_instance: bool = False


def _safe_file_stem(text: str, default: str = "service") -> str:
    value = re.sub(r'[\\/:*?"<>|\s]+', "_", text.strip()).strip("._")
    return value[:60] or default


def _log_path_for_config(cfg: ProgramConfig) -> Path:
    if cfg.id:
        return LOG_DIR / f"service_{_safe_file_stem(cfg.id)}.log"
    key = f"{cfg.service_type}|{cfg.exe_path}|{cfg.name}"
    digest = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return LOG_DIR / f"service_{digest}.log"


def _parse_int_lines(text: str) -> List[int]:
    pids = []
    for line in text.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _parse_process_rows(text: str) -> List[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) not in {2, 3}:
            continue
        try:
            ancestors = []
            if len(parts) == 3 and parts[2].strip():
                ancestors = [int(value) for value in parts[2].split(",") if value.strip().isdigit()]
            rows.append({"pid": int(parts[0]), "parent_pid": int(parts[1]), "ancestors": ancestors})
        except ValueError:
            continue
    return rows


def _is_descendant_process(pid: int, ancestor_pid: int, parent_by_pid: Dict[int, int]) -> bool:
    seen = set()
    current = pid
    while current in parent_by_pid and current not in seen:
        seen.add(current)
        parent = parent_by_pid[current]
        if parent == ancestor_pid:
            return True
        current = parent
    return False


def _query_running_exe_processes(exe_path: Path) -> List[dict]:
    if os.name != "nt":
        return []

    target = str(exe_path.resolve())
    env = os.environ.copy()
    env["WSM_TARGET_PROCESS"] = target
    script = r"""
$target = [System.IO.Path]::GetFullPath($env:WSM_TARGET_PROCESS)
$name = [System.IO.Path]::GetFileName($target)
$processes = @(Get-CimInstance Win32_Process)
$parents = @{}
foreach ($process in $processes) {
    $parents[[int]$process.ProcessId] = [int]$process.ParentProcessId
}
$processes | Where-Object { $_.Name -eq $name } | ForEach-Object {
    if ($_.ExecutablePath) {
        try {
            $path = [System.IO.Path]::GetFullPath($_.ExecutablePath)
            if ([String]::Equals($path, $target, [StringComparison]::OrdinalIgnoreCase)) {
                $ancestors = @()
                $seen = @{}
                $next = [int]$_.ParentProcessId
                while ($next -gt 0 -and -not $seen.ContainsKey($next)) {
                    $ancestors += $next
                    $seen[$next] = $true
                    if (-not $parents.ContainsKey($next)) { break }
                    $next = [int]$parents[$next]
                }
                "$($_.ProcessId)|$($_.ParentProcessId)|$($ancestors -join ',')"
            }
        } catch {}
    }
}
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=CREATE_NO_WINDOW,
            env=env,
            timeout=5,
        )
        if result.returncode == 0:
            return _parse_process_rows(result.stdout)
    except Exception:
        pass
    return []


def _query_running_exe_pids(exe_path: Path) -> List[int]:
    return [row["pid"] for row in _query_running_exe_processes(exe_path)]


def _query_running_jar_pids(jar_path: Path) -> List[int]:
    if os.name != "nt":
        return []

    target = str(jar_path.resolve())
    env = os.environ.copy()
    env["WSM_TARGET_JAR"] = target
    script = r"""
$target = [System.IO.Path]::GetFullPath($env:WSM_TARGET_JAR)
$altTarget = $target.Replace('\', '/')
Get-CimInstance Win32_Process -Filter "Name='java.exe' OR Name='javaw.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($cmd -and ($cmd.IndexOf($target, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                   $cmd.IndexOf($altTarget, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
        $_.ProcessId
    }
}
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=CREATE_NO_WINDOW,
            env=env,
            timeout=5,
        )
        if result.returncode == 0:
            return _parse_int_lines(result.stdout)
    except Exception:
        pass
    return []


def _service_environment():
    # Frozen child applications must not reuse the workbench extraction directory.
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith('_PYI_')
        and key.upper() != '_MEIPASS2'
    }
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


class ManagedProcess:
    def __init__(self, cfg: ProgramConfig, log_callback: Callable, app_ref: App):
        self.cfg, self.log_callback, self.app_ref = cfg, log_callback, app_ref
        self.proc, self._stop_flag, self._is_manual_stop = None, threading.Event(), False
        self._lifecycle_lock = threading.RLock()
        self._generation = 0
        self._restart_requested = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def external_duplicate_pids(self) -> List[int]:
        with self._lifecycle_lock:
            proc = self.proc
            if proc is None or proc.poll() is not None or self.cfg.service_type != "exe":
                return []
            own_pid = proc.pid

        target = self.cfg.exe_path.strip().strip('"')
        if not target:
            return []

        rows = _query_running_exe_processes(Path(target))
        parent_by_pid = {row["pid"]: row["parent_pid"] for row in rows}
        return [
            row["pid"]
            for row in rows
            if row["pid"] != own_pid
            and own_pid not in row.get("ancestors", [])
            and not (
                not row.get("ancestors")
                and _is_descendant_process(row["pid"], own_pid, parent_by_pid)
            )
        ]

    def start(self):
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop_flag.clear()
            self._is_manual_stop = False
            self._restart_requested = False
            target = self.cfg.exe_path.strip().strip('"')
            if self.cfg.service_type == "jar":
                jar_path = Path(target)
                if not target or not jar_path.is_file():
                    raise FileNotFoundError(f"未找到 JAR 文件: {target}")
                running_pids = _query_running_jar_pids(jar_path)
                if running_pids:
                    raise RuntimeError(f"该 JAR 已在运行，PID: {', '.join(map(str, running_pids))}，不能重复启动。")
                cmd, workdir = build_jar_command(
                    JarServiceConfig(target, self.cfg.args, self.cfg.jvm_args, self.cfg.java_path))
            else:
                exe_path = Path(target)
                if not target or not exe_path.is_file():
                    raise FileNotFoundError(f"未找到 EXE 文件: {target}")
                running_pids = _query_running_exe_pids(exe_path)
                if running_pids:
                    raise RuntimeError(f"该程序已在运行，PID: {', '.join(map(str, running_pids))}，不能重复启动。")
                cmd, workdir = [target, *shlex.split(self.cfg.args, posix=False)], exe_path.parent
            env = _service_environment()
            try:
                account = getpass.getuser()
            except Exception:
                account = '(无法读取)'
            diagnostics = [
                f"工作台版本: {APP_VERSION}",
                f"运行账号: {account}",
                f"目标文件: {target}",
                f"工作目录: {workdir}",
                f"TEMP: {env.get('TEMP', '(未设置)')}",
                f"TMP: {env.get('TMP', '(未设置)')}",
                f"工作台临时目录: {tempfile.gettempdir()}",
                f"工作台资源目录: {RESOURCE_DIR}",
                "子进程打包环境: 已清理内部变量，RESET_ENVIRONMENT=1",
            ]
            if os.name == 'nt':
                try:
                    import ctypes
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
                    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
                    buffer = ctypes.create_unicode_buffer(32768)
                    length = kernel.GetDllDirectoryW(len(buffer), buffer)
                    dll_dir = buffer.value if length else '(默认搜索目录)'
                    diagnostics.append(f"父进程 DLL 搜索目录: {dll_dir}")
                except Exception as exc:
                    diagnostics.append(f"DLL 目录读取失败: {type(exc).__name__}")
            for detail in diagnostics:
                self.log_callback(f"--- [启动诊断] {detail} ---\n")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                text=False,
                cwd=str(workdir),
                env=env,
            )
            self.proc = proc
            self._generation += 1
            generation = self._generation
        self.log_callback(f"--- [系统] 进程已创建 (PID: {proc.pid})，等待程序初始化 ---\n")
        threading.Thread(target=self._read_loop, args=(proc, generation), daemon=True).start()

    def _read_loop(self, proc, generation):
        try:
            while not self._stop_flag.is_set():
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None: break
                    time.sleep(0.05);
                    continue
                try:
                    msg = line.decode("gbk")
                except UnicodeDecodeError:
                    msg = line.decode("utf-8", errors="replace")
                self.log_callback(msg)
        finally:
            code = proc.poll()
            self.log_callback(f"\n--- [系统] 进程已退出 (code={code}) ---\n")
            with self._lifecycle_lock:
                is_current = self.proc is proc and self._generation == generation
                if (is_current and not self._is_manual_stop
                        and not self.app_ref._closing
                        and self.app_ref.config_obj.auto_restart_on_crash):
                    self._restart_requested = True

    def consume_restart_request(self) -> bool:
        with self._lifecycle_lock:
            requested = self._restart_requested
            self._restart_requested = False
            return requested

    def stop(self):
        with self._lifecycle_lock:
            self._is_manual_stop = True
            self._restart_requested = False
            self._stop_flag.set()
            self._generation += 1
            proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
        finally:
            with self._lifecycle_lock:
                if self.proc is proc:
                    self.proc = None


class ProgramTab:
    def __init__(self, parent: tk.Frame, cfg: ProgramConfig, refresh_cb: Callable, app_ref: App):
        self.cfg, self.refresh_cb, self.app_ref = cfg, refresh_cb, app_ref
        self.log_queue = BoundedLogQueue(max_messages=5000)
        self.process = ManagedProcess(cfg, self.log_queue.put, app_ref)
        self.frame = tk.Frame(parent, bg=COLOR_APP_BG)
        self.last_state = False
        self.search_matches = []
        self.current_search_idx = 0
        self._last_duplicate_check = 0
        self._duplicate_checking = False
        self._external_duplicate_pids: List[int] = []
        self._restart_after_id = None
        self._poll_after_id = None
        self._nav_refresh_after_id = None
        self._disposed = False
        self._activation_after_id = None
        self._follow_latest = True
        self.last_error = ""
        self._build_ui()
        self._load_saved_logs()
        self._poll()

    def _build_ui(self):
        card = tk.Frame(self.frame, bg=COLOR_CARD_BG, relief="flat", bd=0, padx=24, pady=16)
        card.pack(fill="both", expand=True)

        # Row 1
        row1 = tk.Frame(card, bg=COLOR_CARD_BG);
        row1.pack(fill="x", pady=(0, 10))
        row1.columnconfigure((0, 1), weight=1, uniform='fields')
        name_wrap = tk.Frame(row1, bg=COLOR_CARD_BG);
        name_wrap.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        name_head = tk.Frame(name_wrap, bg=COLOR_CARD_BG);
        name_head.pack(fill="x")
        tk.Label(name_head, text="服务名称", bg=COLOR_CARD_BG, fg=COLOR_TEXT_BODY, font=(FONT_FAMILY, 9)).pack(
            side="left", pady=(0, 8))
        self.name_var = tk.StringVar(value=self.cfg.name)
        self.name_entry = make_entry(name_wrap, self.name_var)
        self.name_entry.pack(fill="x")

        arg_wrap = tk.Frame(row1, bg=COLOR_CARD_BG);
        arg_wrap.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        arg_head = tk.Frame(arg_wrap, bg=COLOR_CARD_BG);
        arg_head.pack(fill="x")
        tk.Label(arg_head, text="程序参数", bg=COLOR_CARD_BG, fg=COLOR_TEXT_BODY, font=(FONT_FAMILY, 9)).pack(
            side="left", pady=(0, 8))
        q = tk.Label(arg_head, text=" (?)", bg=COLOR_CARD_BG, fg=COLOR_PRIMARY, font=(FONT_FAMILY, 9, "bold"),
                     cursor="hand2");
        q.pack(side="left", pady=(0, 8))
        ToolTip(q, '示例: --port 8080')
        self.args_var = tk.StringVar(value=self.cfg.args)
        self.args_entry = make_entry(arg_wrap, self.args_var)
        self.args_entry.pack(fill="x")

        # Row 2
        row2 = tk.Frame(card, bg=COLOR_CARD_BG);
        row2.pack(fill="x", pady=(0, 10))
        type_wrap = tk.Frame(row2, bg=COLOR_CARD_BG);
        type_wrap.pack(side="left", padx=(0, 14))
        tk.Label(type_wrap, text="运行类型", bg=COLOR_CARD_BG, fg=COLOR_TEXT_BODY, font=(FONT_FAMILY, 9)).pack(
            side="left")
        self.type_var = tk.StringVar(value=self.cfg.service_type)
        self.type_box = ctk.CTkComboBox(type_wrap, width=90, height=30, state='readonly', values=['exe', 'jar'],
                                      variable=self.type_var, command=lambda _: self._on_type_changed(),
                                      fg_color='#FFFFFF', text_color=COLOR_TEXT_TITLE, border_color=COLOR_BORDER,
                                      button_color=COLOR_BTN_SECONDARY, button_hover_color=COLOR_BTN_SECONDARY_HOVER,
                                      border_width=1, corner_radius=6, font=(FONT_FAMILY, 13))
        self.type_box.pack(side="left", padx=(8, 0));
        self.auto_start_var = tk.BooleanVar(value=self.cfg.auto_start_instance)
        tk.Checkbutton(row2, text="自启实例", variable=self.auto_start_var, bg=COLOR_CARD_BG, fg=COLOR_PRIMARY,
                       font=(FONT_FAMILY, 9)).pack(side="right")

        # Path Row
        path_row = tk.Frame(card, bg=COLOR_CARD_BG);
        path_row.pack(fill="x", pady=(0, 10))
        self.path_var = tk.StringVar(value=self.cfg.exe_path)
        self.path_entry = make_entry(path_row, self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True)
        make_button(path_row, "浏览", self._browse, py=8).pack(side="left", padx=(10, 0))

        # Java Rows
        self.java_row = tk.Frame(card, bg=COLOR_CARD_BG);
        self.java_row.pack(fill="x", pady=(0, 10))
        tk.Label(self.java_row, text="Java 命令", bg=COLOR_CARD_BG, fg=COLOR_TEXT_BODY, font=(FONT_FAMILY, 9)).pack(
            side="left")
        self.java_var = tk.StringVar(value=self.cfg.java_path);
        make_entry(self.java_row, self.java_var).pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.jvm_row = tk.Frame(card, bg=COLOR_CARD_BG);
        self.jvm_row.pack(fill="x", pady=(0, 14))
        tk.Label(self.jvm_row, text="JVM 参数", bg=COLOR_CARD_BG, fg=COLOR_TEXT_BODY, font=(FONT_FAMILY, 9)).pack(
            side="left")
        self.btn_jar_template = make_button(
            self.jvm_row, "内存模板", self._show_jar_template_menu,
            px=10, py=6, font_size=9,
        )
        self.btn_jar_template.pack(side="right", padx=(10, 0))
        self.jvm_args_var = tk.StringVar(value=self.cfg.jvm_args);
        self.jvm_entry = make_entry(self.jvm_row, self.jvm_args_var)
        self.jvm_entry.pack(side="left", fill="x", expand=True, padx=(10, 0))
        ToolTip(self.jvm_entry, '-Xms 设置初始堆内存，-Xmx 设置最大堆内存；例如 -Xms256m -Xmx512m')

        # Action Buttons
        action_row = tk.Frame(card, bg=COLOR_CARD_BG);
        self.action_row = action_row
        action_row.pack(fill="x", pady=(0, 10))
        self.status_tag = WorkbenchBadge(action_row, text="● 已停止", text_color=COLOR_STOP, fg_color=COLOR_STOP_BG,
                                        font=(FONT_FAMILY, 12), width=104, height=36, corner_radius=6)
        self.status_tag.pack(side="left", padx=(0, 12))
        self.btn_start = make_button(action_row, "启动服务", self.request_start, primary=True);
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop = make_button(action_row, "停止服务", self.stop, danger=True);
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_restart = make_button(action_row, "重启服务", self.restart, px=14, py=8)
        self.btn_restart.pack(side="left")


        # Search Bar
        self.search_bar = tk.Frame(card, bg="#FFFFFF", highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.search_var = tk.StringVar();
        self.search_var.trace_add("write", lambda *_: self._perform_search())
        tk.Label(self.search_bar, text=" 查找 ", bg="#FFFFFF", fg=COLOR_TEXT_BODY,
                 font=(FONT_FAMILY, 8)).pack(side="left")
        self.search_entry = tk.Entry(self.search_bar, textvariable=self.search_var, relief="flat",
                                     font=(FONT_FAMILY, 9))
        self.search_entry.pack(side="left", fill="x", expand=True, padx=5, pady=5)
        self.search_status_lbl = tk.Label(self.search_bar, text="0/0", bg="#FFFFFF", fg=COLOR_TEXT_MUTED,
                                          font=(FONT_FAMILY, 8));
        self.search_status_lbl.pack(side="left", padx=5)
        for i, cmd in [(" ∧ ", -1), (" ∨ ", 1)]:
            lb = tk.Label(self.search_bar, text=i, bg="#FFFFFF", cursor="hand2");
            lb.pack(side="left", padx=2)
            lb.bind("<Button-1>", lambda e, c=cmd: self._jump_search(c))
        close_lb = tk.Label(self.search_bar, text=" ✕ ", bg="#FFFFFF", cursor="hand2");
        close_lb.pack(side="left", padx=5)
        close_lb.bind("<Button-1>", lambda _: self._hide_search())

        # Log Toolbar
        log_toolbar = tk.Frame(card, bg=COLOR_CARD_BG)
        log_toolbar.pack(fill="x", pady=(5, 0))
        tk.Label(
            log_toolbar,
            text="日志",
            fg=COLOR_TEXT_BODY,
            bg=COLOR_CARD_BG,
            font=(FONT_FAMILY, 9, "bold")
        ).pack(side="left")
        make_button(log_toolbar, "搜索", self._show_search, px=10, py=3, font_size=9).pack(side="right", padx=(8, 0))
        make_button(log_toolbar, "导出日志", self.export_logs, px=10, py=3, font_size=9).pack(side="right", padx=(8, 0))
        make_button(log_toolbar, "清空日志", self.clear_logs, danger=True, px=10, py=3, font_size=9).pack(side="right")

        log_wrap = tk.Frame(card, bg=COLOR_BORDER, highlightthickness=1, highlightbackground=COLOR_BORDER);
        log_wrap.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(log_wrap, bg=COLOR_LOG_BG, fg=COLOR_LOG_TEXT, insertbackground="#FFFFFF", relief="flat",
                                bd=0, padx=14, pady=12, font=("Consolas", 10), state="disabled", wrap="none")
        self.log_scroll = ttk.Scrollbar(
            log_wrap,
            orient="vertical",
            command=self.log_text.yview,
            style="Log.Vertical.TScrollbar",
        )
        self.log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=self._on_log_yview)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.tag_configure("match", background=COLOR_SEARCH_HIGHLIGHT, foreground="#000000")
        self.log_text.tag_configure("current", background=COLOR_SEARCH_CURRENT, foreground="#FFFFFF")

        # 绑定到 log_text 控件本身（需要焦点在日志框时触发）
        self.log_text.bind("<Control-l>", lambda _: self.clear_logs())
        self.log_text.bind("<Control-L>", lambda _: self.clear_logs())
        # 绑定到当前页签的主容器
        self.frame.bind("<Control-f>", lambda _: self._show_search())
        # 同时为了确保焦点在日志框时也能触发，也给 log_text 绑定一个
        self.log_text.bind("<Control-f>", lambda _: self._show_search())
        # self.frame.bind_all("<Control-f>", lambda _: self._show_search())
        self.search_entry.bind("<Escape>", lambda _: self._hide_search())

        self._on_type_changed()
        self.name_var.trace_add("write", self._on_name_changed)
        for variable in (
            self.args_var,
            self.type_var,
            self.path_var,
            self.java_var,
            self.jvm_args_var,
            self.auto_start_var,
        ):
            variable.trace_add("write", self._on_config_changed)

    def _on_config_changed(self, *_):
        self.sync()
        self.app_ref.schedule_save()

    def _on_name_changed(self, *_):
        self.sync()
        self.app_ref.schedule_save()
        if self._nav_refresh_after_id is not None:
            try:
                self.frame.after_cancel(self._nav_refresh_after_id)
            except tk.TclError:
                pass
        self._nav_refresh_after_id = self.frame.after(120, self._refresh_nav_after_edit)

    def _refresh_nav_after_edit(self):
        self._nav_refresh_after_id = None
        if not self._disposed:
            self.refresh_cb()

    def _show_search(self):
        self.search_bar.pack(before=self.log_text.master, fill="x", pady=(0, 5)); self.search_entry.focus_set()

    def _hide_search(self):
        self.search_bar.pack_forget()
        self.search_var.set("")
        self.log_text.tag_remove("match", "1.0", "end")
        self.log_text.tag_remove("current", "1.0", "end")
        self.search_status_lbl.config(text="0/0")

    def _perform_search(self):
        self.log_text.tag_remove("match", "1.0", "end");
        self.log_text.tag_remove("current", "1.0", "end");
        query = self.search_var.get();
        self.search_matches = []
        self.current_search_idx = 0
        if not query:
            self.search_status_lbl.config(text="0/0")
            return
        idx = "1.0"
        while True:
            idx = self.log_text.search(query, idx, nocase=True, stopindex="end")
            if not idx: break
            end = f"{idx}+{len(query)}c";
            self.search_matches.append((idx, end))
            self.log_text.tag_add("match", idx, end);
            idx = end
        if self.search_matches:
            self._update_search_ui()
        else:
            self.search_status_lbl.config(text="0/0")

    def _jump_search(self, d):
        if not self.search_matches: return
        self.current_search_idx = (self.current_search_idx + d) % len(self.search_matches);
        self._update_search_ui()

    def _update_search_ui(self):
        self.log_text.tag_remove("current", "1.0", "end")
        s, e = self.search_matches[self.current_search_idx]
        self.log_text.tag_add("current", s, e);
        self.log_text.see(s)
        self._follow_latest = False
        self.search_status_lbl.config(text=f"{self.current_search_idx + 1}/{len(self.search_matches)}")

    def _on_log_yview(self, first, last):
        self.log_scroll.set(first, last)
        self._follow_latest = float(last) >= 0.999

    def _sync_follow_state(self, *_):
        _, bottom = self.log_text.yview()
        self._follow_latest = bottom >= 0.999

    def show_latest(self):
        self.log_text.yview_moveto(1.0)
        self._follow_latest = True

    def _flush_log_queue(self):
        messages = self.log_queue.drain(LOG_UI_BATCH_SIZE)
        if messages:
            follow_latest = self._follow_latest
            text = "".join(messages)
            self.log_text.config(state="normal")
            self.log_text.insert("end", text)
            self._trim_log_widget()
            if self.frame.winfo_ismapped() and follow_latest:
                self.show_latest()
            self.log_text.config(state="disabled")
            self._append_log_file(text)

    def _trim_log_widget(self):
        end_index = self.log_text.index("end-1c")
        line_count = int(end_index.split(".", 1)[0])
        if line_count > MAX_UI_LOG_LINES:
            remove_through = line_count - MAX_UI_LOG_LINES + 1
            self.log_text.delete("1.0", f"{remove_through}.0")
        char_count = self.log_text.count("1.0", "end-1c", "chars")
        if char_count and char_count[0] > MAX_UI_LOG_CHARS:
            self.log_text.delete("1.0", f"end-{MAX_UI_LOG_CHARS}c")

    def _current_log_path(self) -> Path:
        self.sync()
        return _log_path_for_config(self.cfg)

    def _append_log_file(self, text: str):
        try:
            append_rotating_log(
                self._current_log_path(),
                text,
                max_bytes=MAX_DISK_LOG_BYTES,
                backups=LOG_BACKUPS,
            )
        except Exception:
            pass

    def _load_saved_logs(self):
        path = _log_path_for_config(self.cfg)
        if not path.exists():
            return
        try:
            data = path.read_bytes()
            if len(data) > MAX_UI_LOG_CHARS:
                data = data[-MAX_UI_LOG_CHARS:]
                prefix = "--- [系统] 仅加载最近约 500KB 历史日志 ---\n"
            else:
                prefix = "--- [系统] 已加载历史日志 ---\n"
            content = data.decode("utf-8", errors="replace")
        except Exception:
            return

        self.log_text.config(state="normal")
        self.log_text.insert("end", prefix)
        self.log_text.insert("end", content)
        self._trim_log_widget()
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll(self):
        if self._disposed:
            return
        self._flush_log_queue()
        if self.process.consume_restart_request() and self._restart_after_id is None:
            self.log_queue.put("--- [系统] 检测到异常退出，3 秒后自动重启 ---\n")
            self._restart_after_id = self.frame.after(3000, self._restart_after_crash)
        if self._external_duplicate_pids:
            pids = self._external_duplicate_pids
            self._external_duplicate_pids = []
            if self.process.running:
                self.log_queue.put(
                    f"--- [系统] 检测到外部已启动同一程序 (PID: {', '.join(map(str, pids))})，"
                    "已自动停止工作台启动的实例 ---\n"
                )
                self.process.stop()

        running = self.process.running
        if running != self.last_state: self.last_state = running; self.refresh_cb()
        if running:
            self._schedule_duplicate_check()
            self.status_tag.config(text="● 运行中", fg=COLOR_SUCCESS, bg=COLOR_SUCCESS_BG)
            self.btn_start.config(state="disabled", bg="#E5E6EB");
            self.btn_stop.config(state="normal", bg="#FDECEC")
            self.btn_restart.config(state="normal")
        else:
            target = self.path_var.get().strip().strip('"')
            missing = bool(target) and not Path(target).is_file()
            if missing:
                self.status_tag.config(text="! 文件不存在", fg=COLOR_WARNING, bg=COLOR_WARNING_BG)
            elif self.last_error:
                self.status_tag.config(text="! 启动失败", fg=COLOR_DANGER, bg=COLOR_ERROR_BG)
            else:
                self.status_tag.config(text="● 已停止", fg=COLOR_STOP, bg=COLOR_STOP_BG)
            self.btn_start.config(state="normal", bg=COLOR_PRIMARY);
            self.btn_stop.config(state="disabled", bg="#F5F6F7")
            self.btn_restart.config(state="disabled")
        self._poll_after_id = self.frame.after(200, self._poll)

    def dispose(self, stop_process=True):
        self._disposed = True
        for after_id in (self._poll_after_id, self._restart_after_id, self._nav_refresh_after_id, self._activation_after_id):
            if after_id is None:
                continue
            try:
                self.frame.after_cancel(after_id)
            except tk.TclError:
                pass
        self._poll_after_id = None
        self._restart_after_id = None
        self._nav_refresh_after_id = None
        if stop_process:
            self.process.stop()

    def _restart_after_crash(self):
        self._restart_after_id = None
        if self.app_ref._closing or not self.app_ref.config_obj.auto_restart_on_crash:
            return
        self.start(show_error=False)

    def _schedule_duplicate_check(self):
        now = time.monotonic()
        if self._duplicate_checking or now - self._last_duplicate_check < 10:
            return

        self._last_duplicate_check = now
        self._duplicate_checking = True

        def worker():
            try:
                pids = self.process.external_duplicate_pids()
                if pids:
                    self._external_duplicate_pids = pids
            finally:
                self._duplicate_checking = False

        threading.Thread(target=worker, daemon=True).start()

    def _on_type_changed(self):
        is_jar = self.type_var.get() == "jar"
        if is_jar:
            self.java_row.pack(before=self.action_row, fill="x", pady=(0, 10))
            self.jvm_row.pack(before=self.action_row, fill="x", pady=(0, 14))
            self.btn_jar_template.config(state="normal")
        else:
            self.java_row.pack_forget(); self.jvm_row.pack_forget(); self.btn_jar_template.config(state="disabled")

    def _browse(self):
        p = filedialog.askopenfilename(
            filetypes=[("Target", "*.jar" if self.type_var.get() == "jar" else "*.exe"), ("All", "*.*")])
        if p: self.path_var.set(p)

    def _show_jar_template_menu(self):
        m = tk.Menu(self.frame, tearoff=0)
        for k, v in JAR_JVM_TEMPLATES.items(): m.add_command(label=k, command=lambda val=v: self.jvm_args_var.set(val))
        try:
            m.tk_popup(self.btn_jar_template.winfo_rootx(),
                       self.btn_jar_template.winfo_rooty() + self.btn_jar_template.winfo_height())
        finally:
            m.grab_release()

    def sync(self):
        self.cfg.name, self.cfg.service_type = self.name_var.get(), self.type_var.get()
        self.cfg.exe_path, self.cfg.args = self.path_var.get(), self.args_var.get()
        self.cfg.jvm_args, self.cfg.java_path = self.jvm_args_var.get(), self.java_var.get()
        self.cfg.auto_start_instance = self.auto_start_var.get()

    def start(self, show_error=True):
        self.sync();
        try:
            self.process.start()
            self.last_error = ""
            self.app_ref._set_status(f"“{self.cfg.name}”已启动")
            return True
        except Exception as e:
            self.last_error = str(e)
            self.log_queue.put(f"--- [系统] 启动失败：{e} ---\n")
            if show_error:
                messagebox.showerror("启动失败", str(e))
            return False

    def request_start(self):
        if messagebox.askyesno('启动服务', f'确定启动“{self.name_var.get()}”吗？', parent=self.frame, default='no'):
            self.start()

    def stop(self, confirm=True):
        if confirm and not messagebox.askyesno('停止服务', f'确定停止“{self.name_var.get()}”及其子进程吗？', parent=self.frame, default='no'):
            return
        if self._restart_after_id is not None:
            try:
                self.frame.after_cancel(self._restart_after_id)
            except tk.TclError:
                pass
            self._restart_after_id = None
        self.process.stop()
        self.app_ref._set_status(f"“{self.cfg.name}”已停止")

    def restart(self):
        if not self.process.running:
            return
        if not messagebox.askyesno('重启服务', f'确定重启“{self.name_var.get()}”吗？服务将暂时中断。', parent=self.frame, default='no'):
            return
        self.stop(confirm=False)
        self._restart_after_id = self.frame.after(1000, self._restart_after_manual)

    def _restart_after_manual(self):
        self._restart_after_id = None
        self.start()

    def clear_logs(self):
        """清空当前实例的日志显示"""
        if not messagebox.askyesno('清空日志', f'确定清空“{self.name_var.get()}”的显示日志和当前日志文件吗？此操作无法撤销。', parent=self.frame, default='no'):
            return
        # 1. 解锁 Text 控件
        self.log_text.config(state="normal")
        # 2. 删除所有内容 (1.0 表示第一行第0列到末尾)
        self.log_text.delete("1.0", "end")
        # 3. 重新锁定 Text 控件
        self.log_text.config(state="disabled")

        # 4. 清空还没来得及打印到屏幕上的队列数据
        self.log_queue.clear()
        self.search_var.set("")
        self.search_matches = []
        self.current_search_idx = 0
        self.search_status_lbl.config(text="0/0")
        self.log_text.tag_remove("match", "1.0", "end")
        self.log_text.tag_remove("current", "1.0", "end")
        try:
            self._current_log_path().unlink(missing_ok=True)
        except Exception:
            pass

    def export_logs(self):
        """导出当前实例日志到文本文件"""
        self._flush_log_queue()
        content = self.log_text.get("1.0", "end-1c")
        if not content.strip():
            messagebox.showinfo("导出日志", "当前没有可导出的日志。")
            return

        safe_name = _safe_file_stem(self.name_var.get(), "service")
        default_name = f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        path = filedialog.asksaveasfilename(
            title="导出日志",
            defaultextension=".log",
            initialfile=default_name,
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if not path:
            return

        try:
            Path(path).write_text(content, encoding="utf-8")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))
            return

        messagebox.showinfo("导出成功", f"日志已保存到:\n{path}")
# --- 主窗口 ---
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"服务进程管理工作台 {APP_VERSION}")
        self.root.minsize(960, 640)
        self._closing = False
        self._save_after_id = None
        self._startup_after_ids = []
        self.config_store = ConfigStore(DATA_DIR, [BASE_DIR / "manager_config.json"])

        self._init_app_icons()
        self._setup_style()
        self.config_obj = self._load_cfg()
        if IS_FROZEN and self.config_obj.sys_auto_start:
            try:
                AutoStartManager.set_status(True)
            except Exception as exc:
                self._load_warning = f"开机自启入口更新失败：{exc}"
        try:
            self.root.geometry(self.config_obj.window_geometry)
        except Exception:
            pass
        self.tabs: List[ProgramTab] = []
        self.current_idx = -1
        self._build_ui()
        if self._load_warning:
            self._set_status(self._load_warning, warning=True)
        auto_tabs = [tab for tab in self.tabs if tab.cfg.auto_start_instance]
        self._schedule_start_tabs(
            auto_tabs,
            self.config_obj.startup_delay_sec * 1000,
            self.config_obj.startup_interval_sec * 1000,
        )

    def _init_app_icons(self):
        """ 初始化并强制刷新窗口图标及任务栏图标 """
        ico_path = RESOURCE_DIR / "window.ico"
        if not ico_path.exists():
            return

        ico_str = str(ico_path)
        try:
            self.root.iconbitmap(ico_str)
        except Exception as e:
            print(f"图标加载异常: {e}")

    def _setup_style(self):
        s = ttk.Style();
        s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=COLOR_INPUT_BG, background="#FFFFFF", foreground=COLOR_TEXT_TITLE)
        s.map("TCombobox", fieldbackground=[("readonly", COLOR_INPUT_BG)], foreground=[("readonly", COLOR_TEXT_TITLE)])
        s.layout(
            "Log.Vertical.TScrollbar",
            [("Vertical.Scrollbar.trough", {
                "sticky": "ns",
                "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
            })],
        )
        s.configure(
            "Log.Vertical.TScrollbar",
            width=7,
            gripcount=0,
            troughcolor=COLOR_LOG_BG,
            background="#343B47",
            bordercolor=COLOR_LOG_BG,
            lightcolor="#343B47",
            darkcolor="#343B47",
        )
        s.map("Log.Vertical.TScrollbar", background=[("active", "#566171")])

    def _load_cfg(self):
        result = self.config_store.load()
        self._load_warning = result.warning
        data = result.data
        migrated_logs = self._migrate_legacy_logs(data["tabs"])
        if migrated_logs:
            notice = f"已迁移 {migrated_logs} 个旧版历史日志"
            self._load_warning = f"{self._load_warning}；{notice}" if self._load_warning else notice
        return AppConfig(
            tabs=[ProgramConfig(**tab) for tab in data["tabs"]],
            sys_auto_start=data["sys_auto_start"],
            auto_restart_on_crash=data["auto_restart_on_crash"],
            window_geometry=data["window_geometry"],
            startup_delay_sec=data["startup_delay_sec"],
            startup_interval_sec=data["startup_interval_sec"],
            update_proxy=data['update_proxy'],
        )

    def _migrate_legacy_logs(self, tabs: List[dict]) -> int:
        migrated = 0
        legacy_log_dirs = [BASE_DIR / "logs"]
        for tab in tabs:
            service_id = str(tab.get("id", "")).strip()
            if not service_id:
                continue
            destination = LOG_DIR / f"service_{_safe_file_stem(service_id)}.log"
            if destination.exists():
                continue
            key = f"{tab.get('service_type', 'exe')}|{tab.get('exe_path', '')}|{tab.get('name', '')}"
            digest = hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:10]
            legacy_name = f"{_safe_file_stem(str(tab.get('name', '')))}_{digest}.log"
            for legacy_dir in legacy_log_dirs:
                source = legacy_dir / legacy_name
                if not source.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                migrated += 1
                break
        return migrated

    def _build_ui(self):
        sidebar = tk.Frame(self.root, bg=COLOR_SIDEBAR_BG, width=228, highlightthickness=1,
                           highlightbackground=COLOR_BORDER);
        sidebar.pack(side="left", fill="y");
        sidebar.pack_propagate(False)
        title_wrap = tk.Frame(sidebar, bg=COLOR_SIDEBAR_BG, padx=16, pady=18)
        title_wrap.pack(fill="x")
        tk.Label(title_wrap, text="服务进程", bg=COLOR_SIDEBAR_BG, fg=COLOR_TEXT_TITLE,
                 font=(FONT_FAMILY, 12, "bold")).pack(side="left")
        self.service_count_label = tk.Label(title_wrap, text="0", bg="#F2F3F5", fg=COLOR_TEXT_MUTED,
                                            font=(FONT_FAMILY, 8), padx=7, pady=2)
        self.service_count_label.pack(side="right")
        list_host = tk.Frame(sidebar, bg=COLOR_SIDEBAR_BG)
        list_host.pack(fill="both", expand=True, padx=8)
        self.tab_canvas = tk.Canvas(list_host, bg=COLOR_SIDEBAR_BG, highlightthickness=0, bd=0)
        self.tab_canvas.pack(fill="both", expand=True)
        self.tab_list_frame = tk.Frame(self.tab_canvas, bg=COLOR_SIDEBAR_BG)
        self._tab_canvas_window = self.tab_canvas.create_window((0, 0), window=self.tab_list_frame, anchor="nw")
        self.tab_list_frame.bind(
            "<Configure>",
            self._queue_sidebar_layout,
        )
        self.tab_canvas.bind(
            "<Configure>",
            self._queue_sidebar_layout,
        )
        self.tab_canvas.bind("<Enter>", lambda _: self.tab_canvas.bind_all("<MouseWheel>", self._scroll_tabs))
        self.tab_canvas.bind("<Leave>", lambda _: self.tab_canvas.unbind_all("<MouseWheel>"))
        bot = tk.Frame(sidebar, bg=COLOR_SIDEBAR_BG, padx=10, pady=10);
        bot.pack(side="bottom", fill="x")
        make_button(bot, "新增服务", self.add_tab, px=14, py=6).pack(fill="x")
        make_button(bot, "系统设置", self.show_settings, ghost=True, px=14, py=6).pack(fill="x", pady=(4, 0))

        main = tk.Frame(self.root, bg=COLOR_APP_BG)
        main.pack(side="left", fill="both", expand=True)
        toolbar = tk.Frame(main, bg="#FFFFFF", padx=24, pady=12)
        toolbar.pack(fill="x")
        tk.Frame(main, bg=COLOR_BORDER, height=1).pack(fill='x')
        self.running_summary_label = tk.Label(toolbar, text="运行 0 / 0", bg="#FFFFFF", fg=COLOR_TEXT_BODY,
                                              font=(FONT_FAMILY, 10, "bold"))
        self.running_summary_label.pack(side="left")
        make_button(toolbar, "重启自启项", self.restart_autostart, px=11, py=4).pack(side="right")
        make_button(toolbar, "停止全部", self.stop_all, danger=True, px=11, py=4).pack(side="right", padx=6)
        make_button(toolbar, "启动全部", self.start_all, primary=True, px=11, py=4).pack(side="right")

        self.content_area = tk.Frame(main, bg=COLOR_APP_BG)
        self.status_label = tk.Label(main, text=f"配置目录：{DATA_DIR}", anchor="w", bg="#FFFFFF",
                                     fg=COLOR_TEXT_MUTED, font=(FONT_FAMILY, 8), padx=14, pady=7)
        self.status_label.pack(side="bottom", fill="x")
        self.content_area.pack(fill="both", expand=True)
        for c in self.config_obj.tabs: self._add_tab_obj(c)
        if self.tabs: self.select_tab(0)

    def _scroll_tabs(self, event):
        self.tab_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _queue_sidebar_layout(self, event=None):
        if getattr(self, '_sidebar_layout_after', None) is None:
            self._sidebar_layout_after = self.root.after(32, self._apply_sidebar_layout)

    def _apply_sidebar_layout(self):
        self._sidebar_layout_after = None
        if self._closing:
            return
        width = self.tab_canvas.winfo_width()
        if getattr(self, '_sidebar_width', None) != width:
            self._sidebar_width = width
            self.tab_canvas.itemconfigure(self._tab_canvas_window, width=width)
        self.tab_canvas.configure(scrollregion=self.tab_canvas.bbox('all'))

    def _add_tab_obj(self, cfg):
        t = ProgramTab(self.content_area, cfg, self._render_nav, self)
        self.tabs.append(t);
        self._render_nav()

    def _render_nav(self):
        for w in self.tab_list_frame.winfo_children(): w.destroy()
        for i, t in enumerate(self.tabs):
            active = (i == self.current_idx);
            running = t.process.running
            bg = "#E9E9EC" if active else COLOR_SIDEBAR_BG
            row = ctk.CTkFrame(self.tab_list_frame, fg_color=bg, corner_radius=8, height=40, cursor="hand2");
            row.pack(fill="x", pady=2)
            dot = tk.Label(row, text="●", bg=bg, fg=COLOR_SUCCESS if running else "#B1B1B9", font=("Arial", 9),
                           padx=9)
            dot.pack(side="left", padx=(4, 0), pady=5)
            service_name = t.name_var.get().strip() or "未命名服务"
            lbl = tk.Label(row, text=service_name, bg=bg, fg=COLOR_PRIMARY if active else COLOR_TEXT_TITLE,
                           font=(FONT_FAMILY, 9), anchor="w", pady=6)
            lbl.pack(side="left", fill="x", expand=True)
            ToolTip(lbl, service_name)
            if len(self.tabs) > 1:
                cb = tk.Label(row, text="×", bg=bg, fg=COLOR_TEXT_MUTED);
                cb.pack(side="right", padx=8)
                cb.bind("<Button-1>", lambda e, idx=i: self.delete_tab(idx))
            for w in (row, lbl, dot): w.bind("<Button-1>", lambda e, idx=i: self.select_tab(idx))
        running_count = sum(1 for tab in self.tabs if tab.process.running)
        self.service_count_label.config(text=str(len(self.tabs)))
        self.running_summary_label.config(text=f"运行 {running_count} / {len(self.tabs)}")

    def select_tab(self, idx):
        if self.current_idx != -1: self.tabs[self.current_idx].frame.pack_forget()
        self.current_idx = idx;
        self.tabs[idx].frame.pack(fill="both", expand=True);
        tab = self.tabs[idx]
        if tab._activation_after_id is not None:
            tab.frame.after_cancel(tab._activation_after_id)
        def activate():
            tab._activation_after_id = None
            if not self._closing and not tab._disposed and 0 <= self.current_idx < len(self.tabs) and self.tabs[self.current_idx] is tab:
                tab.show_latest()
        tab._activation_after_id = tab.frame.after_idle(activate)
        self._render_nav()

    def add_tab(self):
        self._add_tab_obj(ProgramConfig(name=f"新服务 {len(self.tabs) + 1}"))
        self.select_tab(len(self.tabs) - 1)
        self.schedule_save(immediate=True)

    def delete_tab(self, idx):
        if len(self.tabs) <= 1: return
        target = self.tabs[idx]
        detail = f"确定删除“{target.name_var.get()}”吗？"
        if target.process.running:
            detail += "\n该服务正在运行，删除时会停止整个进程树。"
        if not messagebox.askyesno("删除服务", detail, parent=self.root, default='no'):
            return
        old_current_idx = self.current_idx
        if 0 <= old_current_idx < len(self.tabs):
            self.tabs[old_current_idx].frame.pack_forget()

        self.tabs[idx].dispose()
        self.tabs[idx].frame.destroy();
        self.tabs.pop(idx)

        if idx < old_current_idx:
            next_idx = old_current_idx - 1
        elif idx == old_current_idx:
            next_idx = min(idx, len(self.tabs) - 1)
        else:
            next_idx = old_current_idx

        self.current_idx = -1;
        self.select_tab(max(0, min(next_idx, len(self.tabs) - 1)))
        self.schedule_save(immediate=True)

    def show_settings(self):
        win = ctk.CTkToplevel(self.root);
        win.title("系统设置");
        win.geometry(f"520x460+{self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 520) // 2)}+{self.root.winfo_rooty() + 20}");
        win.minsize(520, 460)
        win.configure(fg_color="#FFFFFF");
        win.transient(self.root)
        tk.Label(win, text="基本设置", font=(FONT_FAMILY, 12, "bold"), bg="#FFFFFF").pack(pady=(20, 15))
        v_auto = tk.BooleanVar(value=self.config_obj.sys_auto_start)

        def toggle_auto():
            requested = v_auto.get()
            if not messagebox.askyesno('修改登录自启', f'确定{"启用" if requested else "关闭"}工作台登录自启吗？', parent=win, default='no'):
                v_auto.set(not requested)
                return
            try:
                AutoStartManager.set_status(requested)
            except Exception as e:
                v_auto.set(not requested)
                messagebox.showerror("失败", str(e))
                return
            self.config_obj.sys_auto_start = requested
            self.schedule_save(immediate=True)

        tk.Checkbutton(win, text="电脑开机后自动启动本工作台", variable=v_auto, command=toggle_auto, bg="#FFFFFF",
                       font=(FONT_FAMILY, 10)).pack(anchor="w", padx=40, pady=5)
        v_restart = tk.BooleanVar(value=self.config_obj.auto_restart_on_crash)

        def toggle_r():
            self.config_obj.auto_restart_on_crash = v_restart.get()
            self.schedule_save(immediate=True)

        tk.Checkbutton(win, text="进程异常退出后自动重启", variable=v_restart, command=toggle_r, bg="#FFFFFF",
                       font=(FONT_FAMILY, 10)).pack(anchor="w", padx=40, pady=5)

        delay_var = tk.IntVar(value=self.config_obj.startup_delay_sec)
        interval_var = tk.IntVar(value=self.config_obj.startup_interval_sec)
        for label, variable in (("工作台启动后等待（秒）", delay_var), ("服务启动间隔（秒）", interval_var)):
            row = tk.Frame(win, bg="#FFFFFF")
            row.pack(fill="x", padx=40, pady=7)
            tk.Label(row, text=label, bg="#FFFFFF", fg=COLOR_TEXT_BODY,
                     font=(FONT_FAMILY, 9)).pack(side="left")
            tk.Spinbox(row, from_=0, to=60, width=5, textvariable=variable,
                       font=(FONT_FAMILY, 9)).pack(side="right")

        def save_timing(*_):
            try:
                self.config_obj.startup_delay_sec = max(0, delay_var.get())
                self.config_obj.startup_interval_sec = max(0, interval_var.get())
            except tk.TclError:
                return
            self.schedule_save()

        delay_var.trace_add("write", save_timing)
        interval_var.trace_add("write", save_timing)

        def check_autostart():
            try:
                valid, command = AutoStartManager.inspect()
                expected = AutoStartManager.expected_command()
                messagebox.showinfo('开机启动检查',
                    f'当前账号登录后自启：{"正确" if valid else "未启用或路径不匹配"}\n'
                    f'注册表命令：{command}\n期望命令：{expected}\n'
                    f'服务等待：{self.config_obj.startup_delay_sec} 秒\n'
                    f'服务间隔：{self.config_obj.startup_interval_sec} 秒\n'
                    'Windows Run 自启在用户登录后执行。', parent=win)
            except Exception as exc:
                messagebox.showerror('自启检查失败', str(exc), parent=win)
        make_button(win, '检查开机启动', check_autostart, px=12, py=6).pack(pady=12)
        tk.Frame(win, bg=COLOR_BORDER, height=1).pack(fill='x', padx=32, pady=8)
        tk.Label(win, text='更新代理', bg='#FFFFFF', fg=COLOR_TEXT_BODY,
                 font=(FONT_FAMILY, 9)).pack(anchor='w', padx=40)
        proxy_var = tk.StringVar(value=self.config_obj.update_proxy)
        proxy_entry = make_entry(win, proxy_var)
        proxy_entry.pack(fill='x', padx=40, pady=(6, 10))
        ToolTip(proxy_entry, '留空：系统代理；direct：直接连接；或填写 http://主机:端口。必须是现场机器能连接的地址。')

        def save_proxy(*_):
            self.config_obj.update_proxy = proxy_var.get().strip()
            self.schedule_save()
        proxy_var.trace_add('write', save_proxy)
        make_button(win, '检查更新', self.check_update, primary=True, px=12, py=6).pack(pady=6)

    def check_update(self):
        if getattr(self, '_update_busy', False):
            return
        if not getattr(sys, 'frozen', False):
            messagebox.showinfo('检查更新', '在线升级仅支持发布版 EXE，源码运行请更新源码。', parent=self.root)
            return
        self._update_busy = True
        proxy = self.config_obj.update_proxy
        progress = ctk.CTkToplevel(self.root)
        progress.title('工作台更新')
        progress.geometry(f'420x180+{self.root.winfo_rootx() + max(0, (self.root.winfo_width() - 420) // 2)}+{self.root.winfo_rooty() + 100}')
        progress.configure(fg_color='#FFFFFF')
        progress.transient(self.root)
        progress.grab_set()
        cancelled = threading.Event()
        label = tk.Label(progress, text='正在检查 GitHub 最新版本…', bg='#FFFFFF', fg=COLOR_TEXT_BODY, padx=20, pady=20)
        label.pack(fill='both', expand=True)
        results = queue.Queue()

        def worker(operation):
            try:
                results.put(('ok', operation()))
            except Exception as exc:
                results.put(('error', str(exc)))

        def finish():
            self._update_busy = False
            if progress.winfo_exists():
                progress.grab_release()
                progress.destroy()

        def cancel():
            cancelled.set()
            progress.grab_release()
            progress.destroy()
            self._set_status('已取消更新，当前版本未替换')
        progress.protocol('WM_DELETE_WINDOW', cancel)
        progress.bind('<Escape>', lambda _: cancel())
        make_button(progress, '取消', cancel, ghost=True, px=18, py=6).pack(pady=(0, 16))

        def poll(stage):
            if self._closing:
                return
            try:
                status, value = results.get_nowait()
            except queue.Empty:
                self.root.after(100, lambda: poll(stage))
                return
            if cancelled.is_set():
                self._update_busy = False
                return
            if status == 'error':
                finish()
                messagebox.showerror('更新失败', f'当前版本未替换。请检查网络或发布文件。\n\n{value}', parent=self.root)
                return
            if stage == 'check':
                if value is None:
                    finish()
                    messagebox.showinfo('检查更新', f'当前 {APP_VERSION} 已是最新版本。', parent=self.root)
                    return
                if any(tab.process.running for tab in self.tabs):
                    finish()
                    messagebox.showinfo('发现新版本', f'发现 {value["version"]}。请先停止全部托管服务，再执行升级。', parent=self.root)
                    return
                if not messagebox.askyesno('确认升级',
                        f'当前 {APP_VERSION} → {value["version"]}\n\n{value["notes"]}\n\n'
                        '升级将关闭并重启工作台，保留配置和日志。新版会按原设置启动自启服务。继续吗？',
                        parent=progress, default='no'):
                    finish()
                    return
                label.config(text='正在下载并校验新版，请稍候…')
                threading.Thread(target=worker, args=(lambda: download_release(value, resolve_data_dir() / 'updates', proxy),), daemon=True).start()
                self.root.after(100, lambda: poll('download'))
                return
            try:
                if any(tab.process.running for tab in self.tabs):
                    raise RuntimeError('下载期间服务已启动，请先停止服务再升级。')
                self.config_store.save(self._collect_config())
                launch_replacement(sys.executable, value, os.getpid(), _service_environment())
            except Exception as exc:
                finish()
                messagebox.showerror('无法升级', str(exc), parent=self.root)
                return
            self._closing = True
            self.dispose(stop_process=False)
            finish()
            self.root.destroy()

        threading.Thread(target=worker, args=(lambda: latest_release(APP_VERSION, proxy),), daemon=True).start()
        self.root.after(100, lambda: poll('check'))

    def _set_status(self, text: str, warning: bool = False, error: bool = False):
        if not hasattr(self, "status_label") or not self.status_label.winfo_exists():
            return
        fg = COLOR_DANGER if error else (COLOR_WARNING if warning else COLOR_TEXT_MUTED)
        bg = COLOR_ERROR_BG if error else (COLOR_WARNING_BG if warning else "#FFFFFF")
        self.status_label.config(text=text, fg=fg, bg=bg)

    def _collect_config(self):
        for tab in self.tabs:
            tab.sync()
        self.config_obj.window_geometry = self.root.geometry()
        return {
            "tabs": [asdict(tab.cfg) for tab in self.tabs],
            "sys_auto_start": self.config_obj.sys_auto_start,
            "auto_restart_on_crash": self.config_obj.auto_restart_on_crash,
            "window_geometry": self.config_obj.window_geometry,
            "startup_delay_sec": self.config_obj.startup_delay_sec,
            "startup_interval_sec": self.config_obj.startup_interval_sec,
            "update_proxy": self.config_obj.update_proxy,
        }

    def schedule_save(self, immediate: bool = False):
        if self._closing:
            return
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
        delay = 0 if immediate else 500
        self._save_after_id = self.root.after(delay, self._save_now)

    def _save_now(self):
        self._save_after_id = None
        try:
            self.config_store.save(self._collect_config())
            self._set_status(f"配置已保存：{CONFIG_PATH}")
        except Exception as exc:
            self._set_status(f"配置保存失败：{CONFIG_PATH}；{exc}", error=True)

    def _cancel_scheduled_starts(self):
        for after_id in self._startup_after_ids:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._startup_after_ids.clear()

    def dispose(self, stop_process=True):
        layout_after = getattr(self, '_sidebar_layout_after', None)
        if layout_after is not None:
            self.root.after_cancel(layout_after)
            self._sidebar_layout_after = None
        self._cancel_scheduled_starts()
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
            self._save_after_id = None
        for tab in self.tabs:
            tab.dispose(stop_process=stop_process)

    def _safe_start(self, tab: ProgramTab):
        if self._closing:
            return
        if not tab.start(show_error=False):
            self._set_status(f"“{tab.cfg.name}”启动失败：{tab.last_error}", error=True)

    def _schedule_start_tabs(self, tabs, initial_delay_ms, interval_ms):
        for tab, delay in zip(tabs, build_start_schedule(len(tabs), initial_delay_ms, interval_ms)):
            after_id = self.root.after(delay, lambda item=tab: self._safe_start(item))
            self._startup_after_ids.append(after_id)

    def start_all(self):
        targets = [tab for tab in self.tabs if not tab.process.running]
        if not targets:
            self._set_status('所有服务已在运行')
            return
        if not messagebox.askyesno('启动全部', f'确定依次启动 {len(targets)} 个未运行的服务吗？', parent=self.root, default='no'):
            return
        self._cancel_scheduled_starts()
        self._schedule_start_tabs(targets, 0, max(250, self.config_obj.startup_interval_sec * 1000))
        self._set_status("正在按顺序启动全部服务")

    def stop_all(self):
        count = sum(tab.process.running for tab in self.tabs)
        if not messagebox.askyesno('停止全部', f'确定停止 {count} 个运行中的服务，并取消待启动任务吗？', parent=self.root, default='no'):
            return
        self._cancel_scheduled_starts()
        for tab in self.tabs:
            tab.stop(confirm=False)
        self._set_status("已停止全部托管服务")

    def restart_autostart(self):
        selected = [tab for tab in self.tabs if tab.auto_start_var.get()]
        if not selected:
            self._set_status('没有勾选自启的服务')
            return
        if not messagebox.askyesno('重启自启项', f'确定重启 {len(selected)} 个自启服务吗？运行中的服务将暂时中断。', parent=self.root, default='no'):
            return
        self._cancel_scheduled_starts()
        for tab in selected:
            tab.stop(confirm=False)
        self._schedule_start_tabs(selected, 1000, max(250, self.config_obj.startup_interval_sec * 1000))
        self._set_status(f"正在重启 {len(selected)} 个自启服务")

    def save_and_exit(self):
        running_count = sum(1 for t in self.tabs if t.process.running)
        if running_count > 0:
            msg = f"当前有 {running_count} 个服务实例正在运行。\n退出管理器将停止所有运行中的服务，是否确定退出？"
        else:
            msg = "确定要关闭服务管理工作台吗？"

        if not messagebox.askyesno("退出确认", msg, parent=self.root, default='no'):
            return
        self._closing = True
        self._cancel_scheduled_starts()
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except tk.TclError:
                pass
        for t in self.tabs:
            t._flush_log_queue()
            t.sync()
        try:
            self.config_store.save(self._collect_config())
        except Exception as exc:
            self._closing = False
            messagebox.showerror("无法退出", f"配置保存失败，为避免丢失修改，工作台没有关闭。\n\n{exc}")
            return

        self.dispose()

        self.root.destroy()


@dataclass
class AppConfig:
    tabs: List[ProgramConfig]
    sys_auto_start: bool
    auto_restart_on_crash: bool
    window_geometry: str
    startup_delay_sec: int = 5
    startup_interval_sec: int = 2
    update_proxy: str = ''


if __name__ == "__main__":
    if os.name == "nt":
        try:
            import ctypes

            # 设置一个独特的 AppID 绕过宿主环境(影刀)的图标劫持
            my_appid = f"Lpbing.ServiceManager.{APP_VERSION}.2026"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(my_appid)
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception as e:
            print(f"Windows 初始化失败: {e}")

    root = ctk.CTk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.save_and_exit)
    root.mainloop()
