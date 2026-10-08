#!/usr/bin/env python3
"""
Windows 黑窗口管理工具

功能:
- 维护任务配置(名称 + 命令 + 终端类型)
- 启动/关闭指定任务
- 批量启动/关闭全部任务
- 查看运行状态

仅依赖 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "profiles.json"
STATE_PATH = BASE_DIR / "runtime_state.json"


CREATE_NEW_CONSOLE = 0x00000010


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_default_profiles() -> Dict:
    data = read_json(CONFIG_PATH, {})
    if data:
        return data

    default = {
        "profiles": [
            {
                "name": "demo-cmd",
                "shell": "cmd",
                "command": "title demo-cmd && echo demo-cmd started",
            },
            {
                "name": "demo-pwsh",
                "shell": "powershell",
                "command": "Write-Host 'demo-pwsh started'",
            },
        ]
    }
    write_json(CONFIG_PATH, default)
    return default


def get_profiles() -> List[Dict]:
    data = ensure_default_profiles()
    return data.get("profiles", [])


def save_profiles(profiles: List[Dict]) -> None:
    write_json(CONFIG_PATH, {"profiles": profiles})


def get_state() -> Dict:
    return read_json(STATE_PATH, {"running": {}})


def save_state(state: Dict) -> None:
    write_json(STATE_PATH, state)


def find_profile(name: str) -> Dict | None:
    for p in get_profiles():
        if p.get("name") == name:
            return p
    return None


def pid_is_alive(pid: int) -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if result.returncode != 0:
        return False
    return str(pid) in result.stdout


def start_profile(name: str) -> None:
    profile = find_profile(name)
    if not profile:
        print(f"[x] 未找到任务: {name}")
        return

    shell = profile.get("shell", "cmd").lower()
    command = profile.get("command", "")

    if shell == "cmd":
        cmd = ["cmd", "/k", command]
    elif shell in {"powershell", "pwsh"}:
        cmd = ["powershell", "-NoExit", "-Command", command]
    else:
        print(f"[x] 不支持的 shell: {shell} (任务: {name})")
        return

    proc = subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
    state = get_state()
    state.setdefault("running", {})
    state["running"][name] = {
        "pid": proc.pid,
        "shell": shell,
        "command": command,
        "started_at": int(time.time()),
    }
    save_state(state)
    print(f"[+] 已启动 {name} (PID: {proc.pid})")


def stop_profile(name: str) -> None:
    state = get_state()
    running = state.get("running", {})
    info = running.get(name)
    if not info:
        print(f"[x] 任务未运行: {name}")
        return

    pid = info.get("pid")
    if not isinstance(pid, int):
        print(f"[x] PID 异常，无法关闭: {name}")
        return

    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    running.pop(name, None)
    save_state(state)
    print(f"[-] 已关闭 {name} (PID: {pid})")


def list_profiles() -> None:
    profiles = get_profiles()
    if not profiles:
        print("暂无任务配置。")
        return
    print("任务列表:")
    for p in profiles:
        print(f"- {p.get('name')} [{p.get('shell', 'cmd')}] => {p.get('command', '')}")


def add_profile(name: str, shell: str, command: str) -> None:
    profiles = get_profiles()
    if any(p.get("name") == name for p in profiles):
        print(f"[x] 已存在同名任务: {name}")
        return
    profiles.append({"name": name, "shell": shell, "command": command})
    save_profiles(profiles)
    print(f"[+] 已新增任务: {name}")


def remove_profile(name: str) -> None:
    profiles = get_profiles()
    next_profiles = [p for p in profiles if p.get("name") != name]
    if len(next_profiles) == len(profiles):
        print(f"[x] 未找到任务: {name}")
        return
    save_profiles(next_profiles)
    print(f"[-] 已删除任务: {name}")


def status() -> None:
    profiles = {p.get("name"): p for p in get_profiles()}
    state = get_state()
    running = state.get("running", {})

    # 清理已退出但残留在 state 的进程记录
    dirty_names = []
    for name, info in running.items():
        pid = info.get("pid")
        if not isinstance(pid, int) or not pid_is_alive(pid):
            dirty_names.append(name)
    for name in dirty_names:
        running.pop(name, None)
    if dirty_names:
        save_state(state)

    print("运行状态:")
    if not profiles:
        print("暂无任务配置。")
        return

    for name, p in profiles.items():
        info = running.get(name)
        if info:
            print(f"- {name}: RUNNING (PID: {info.get('pid')})")
        else:
            print(f"- {name}: STOPPED")


def start_all() -> None:
    for p in get_profiles():
        start_profile(p.get("name", ""))


def stop_all() -> None:
    names = list(get_state().get("running", {}).keys())
    if not names:
        print("当前没有运行中的任务。")
        return
    for name in names:
        stop_profile(name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="黑窗口管理工具")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="列出任务")
    sub.add_parser("status", help="查看运行状态")
    sub.add_parser("start-all", help="启动全部任务")
    sub.add_parser("stop-all", help="关闭全部任务")

    p_start = sub.add_parser("start", help="启动指定任务")
    p_start.add_argument("name")

    p_stop = sub.add_parser("stop", help="关闭指定任务")
    p_stop.add_argument("name")

    p_add = sub.add_parser("add", help="新增任务")
    p_add.add_argument("name")
    p_add.add_argument("shell", choices=["cmd", "powershell", "pwsh"])
    p_add.add_argument("command")

    p_remove = sub.add_parser("remove", help="删除任务")
    p_remove.add_argument("name")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    action = args.action
    if action == "list":
        list_profiles()
    elif action == "status":
        status()
    elif action == "start-all":
        start_all()
    elif action == "stop-all":
        stop_all()
    elif action == "start":
        start_profile(args.name)
    elif action == "stop":
        stop_profile(args.name)
    elif action == "add":
        add_profile(args.name, args.shell, args.command)
    elif action == "remove":
        remove_profile(args.name)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
