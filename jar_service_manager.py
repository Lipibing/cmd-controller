#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jar_service_manager

用于构建 JAR 服务启动命令与基础校验逻辑。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass
class JarServiceConfig:
    jar_path: str
    args: str = ""
    jvm_args: str = ""
    java_path: str = "java"


def build_jar_command(cfg: JarServiceConfig) -> Tuple[List[str], Path]:
    jar = Path(cfg.jar_path.strip().strip('"'))
    if not jar.exists():
        raise FileNotFoundError(f"找不到 JAR 文件: {jar}")
    if jar.suffix.lower() != ".jar":
        raise ValueError(f"不是 JAR 文件: {jar}")

    java_exec = cfg.java_path.strip() or "java"
    jvm_args = shlex.split(cfg.jvm_args, posix=False) if cfg.jvm_args.strip() else []
    app_args = shlex.split(cfg.args, posix=False) if cfg.args.strip() else []
    cmd = [java_exec, *jvm_args, "-jar", str(jar), *app_args]
    return cmd, jar.parent
