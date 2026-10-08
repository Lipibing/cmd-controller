import PyInstaller.__main__
from pathlib import Path

# 获取当前脚本所在目录
base_dir = Path(__file__).resolve().parent
# 你的主程序文件名
main_script = "win_service_manager.py"  # <--- 请修改为你的主程序文件名
# 图标文件名
icon_file = "window.ico"
# 版本号
app_version = "v2.7"
output_name = "服务进程管理工作台"

def build():
    main_script_path = base_dir / main_script
    icon_path = base_dir / icon_file
    if not main_script_path.exists():
        print(f"错误: 找不到主程序文件 {main_script_path}")
        return

    params = [
        str(main_script_path),
        '--noconsole',          # 不显示黑色的控制台窗口
        '--onefile',            # 打包成单个 EXE 文件
        f'--icon={icon_path}',  # 设置 EXE 文件的文件图标
        f'--add-data={icon_path};.',
        f'--name={output_name}',
        '--clean',              # 打包前清理临时文件
        f'--distpath={base_dir / "dist"}',
        f'--workpath={base_dir / "build"}',
        f'--specpath={base_dir}',
        # 如果你的代码里有 import PIL，PyInstaller 通常会自动识别。
        # 如果有额外的资源文件需要打入内部，可以在这里添加 --add-data
    ]

    print(f"正在开始打包 {main_script} ...")
    PyInstaller.__main__.run(params)
    print(f"\n{app_version} 打包完成：{base_dir / 'dist' / (output_name + '.exe')}")

if __name__ == "__main__":
    build()
