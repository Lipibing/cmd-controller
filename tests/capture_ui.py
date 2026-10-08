"""Capture an isolated workbench without starting any real services."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def capture():
    from PIL import ImageGrab
    with tempfile.TemporaryDirectory() as directory:
        os.environ['LOCALAPPDATA'] = directory
        import win_service_manager as wsm
        names = ['北京数据接口', '福建数据抓取', '甘肃能源数据库', '北京交易中心',
                 '陕西智源数据库', '公司端服务', '福建文件同步', '甘肃文件同步', '同步云端服务', '甘肃节点电价', '甘肃 SaaS']
        wsm.ConfigStore(wsm.DATA_DIR).save({'tabs': [
            {'name': name, 'exe_path': sys.executable, 'service_type': 'exe'} for name in names]})
        root = wsm.ctk.CTk()
        app = wsm.App(root)
        root.attributes('-topmost', True)
        output = Path(__file__).resolve().parents[1] / '.package-smoke-ui-v210'
        output.mkdir(exist_ok=True)
        tab = app.tabs[0]
        tab.log_text.config(state='normal')
        tab.log_text.insert('end', '\n'.join(f'2026-10-08 14:30:{i:02d}  INFO  数据同步完成，等待下一次任务' for i in range(36)))
        tab.log_text.config(state='disabled')
        for name, geometry, service_type in (
                ('desktop', '1240x820+40+40', 'exe'),
                ('compact-jar', '960x640+40+40', 'jar')):
            tab.type_var.set(service_type)
            tab._on_type_changed()
            root.geometry(geometry)
            for _ in range(10):
                root.update()
                time.sleep(0.05)
            tab.show_latest()
            root.update()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height())).save(output / f'{name}.png')
        app.show_settings()
        root.attributes('-topmost', False)
        window = [child for child in root.winfo_children() if isinstance(child, wsm.ctk.CTkToplevel)][-1]
        window.attributes('-topmost', True)
        window.lift()
        for _ in range(10):
            root.update()
            time.sleep(0.05)
        x, y = window.winfo_rootx(), window.winfo_rooty()
        ImageGrab.grab((x, y, x + window.winfo_width(), y + window.winfo_height())).save(output / 'settings.png')
        app.dispose(stop_process=False)
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.destroy()
        print(output)


if __name__ == '__main__':
    capture()
