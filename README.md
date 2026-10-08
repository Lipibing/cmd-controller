# 服务进程管理工作台

面向 Windows 现场部署的 EXE / JAR 进程管理工具，集中配置服务、查看日志和执行启停操作。

当前版本：**v2.7**。

## 主要功能

- 管理多个 EXE 和 JAR，保存服务别名、路径、参数和自启设置。
- 单个或批量启动、停止、重启；停止时结束对应进程树。
- 检测重复进程，避免工作台重复启动同一程序。
- 实时日志查看、搜索、清空和导出，支持跟随最新日志。
- JAR 支持 Java 路径、JVM 参数和内存模板。
- 用户登录后自动启动工作台，再按顺序启动指定服务。
- 操作二次确认，配置自动保存并保留备份。

## 使用与部署

发布文件为 `服务进程管理工作台.exe`，现场无需安装 Python。运行 JAR 仍需安装对应版本的 Java。

新增服务后，填写名称、运行类型、程序路径和参数。需要随工作台启动的服务，勾选“自启实例”。

JVM 参数中，`-Xms` 表示初始堆内存，`-Xmx` 表示最大堆内存，例如 `-Xms256m -Xmx512m`。这不是整个 Java 进程的总内存上限。

更新时关闭工作台，用新版 EXE 覆盖原文件，再重新打开。**在线升级尚未接入**，目前仍需手动替换。

## 配置与日志

数据保存在当前 Windows 用户目录下，不随 EXE 替换而丢失：

```text
%LOCALAPPDATA%\ServiceProcessWorkbench\
├── manager_config.json
├── manager_config.backup.json
└── logs\
```

首次运行且用户目录没有配置时，会尝试导入 EXE 同目录的旧版 `manager_config.json`。不同 Windows 账号使用独立配置。

界面日志和内存队列有容量上限；磁盘日志达到 10 MB 后轮转，保留最近 3 份历史文件。

## 登录自启动

在“系统设置”中启用并检查自启动。默认启动工作台后等待 5 秒，再按列表顺序启动勾选的服务，默认间隔 2 秒。

自启动使用当前用户的 Windows Run 注册表项，**需要用户登录**，不是无人登录也会运行的系统服务。移动 EXE 后，应重新设置并检查自启动路径。

## 源码运行与打包

开发环境：Windows、Python 3.10 或更新版本（包含 Tkinter）。

```powershell
# 源码运行
python win_service_manager.py

# 运行测试
python -m unittest discover -s tests -q

# 安装打包工具并生成 EXE
python -m pip install pyinstaller
python build_exe.py
```

输出文件：`dist\服务进程管理工作台.exe`。打包使用 `window.ico` 作为程序图标。

## 项目结构

| 文件 | 作用 |
| --- | --- |
| `win_service_manager.py` | 图形界面、进程管理与登录自启动 |
| `workbench_core.py` | 配置持久化与日志管理 |
| `jar_service_manager.py` | Java 启动命令和 JVM 参数 |
| `build_exe.py` | EXE 打包入口 |
| `cmd_manager.py` | 独立的命令行任务管理工具 |
| `tests/` | 自动化测试 |

## v2.7 变更

修复 JAR 参数行被日志区挤到底部的问题，Java 和 JVM 设置固定在启停按钮上方。“内存模板”移到 JVM 输入框旁。
