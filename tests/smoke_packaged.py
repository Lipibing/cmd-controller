"""Launch only an isolated copy of the packaged EXE and verify its window."""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def smoke():
    base = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='workbench-package-') as directory:
        target = Path(directory) / 'workbench.exe'
        shutil.copy2(base / 'dist' / '服务进程管理工作台.exe', target)
        environment = dict(os.environ, LOCALAPPDATA=directory)
        process = subprocess.Popen([str(target)], env=environment)
        try:
            time.sleep(5)
            query = ("Get-Process | Where-Object { $_.Path -eq '" + str(target).replace("'", "''") +
                     "' } | ForEach-Object { $_.MainWindowTitle }")
            titles = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', query],
                                             encoding='utf-8', errors='replace')
            if 'v2.10' not in titles:
                raise AssertionError('Packaged window not found: ' + titles)
            print('Packaged v2.10 window verified')
        finally:
            if process.poll() is None:
                subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            process.wait(timeout=10)
            time.sleep(1)


if __name__ == '__main__':
    smoke()
