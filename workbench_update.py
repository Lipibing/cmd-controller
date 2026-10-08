"""GitHub release discovery, bounded download and Windows replacement helper."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPOSITORY = 'Lipibing/cmd-controller'
ASSET_NAME = 'ServiceProcessWorkbench.exe'
MAX_DOWNLOAD = 200 * 1024 * 1024


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+(?:\.\d+)?', value):
        raise ValueError('无效版本号')
    parts = tuple(map(int, value.lstrip('v').split('.')))
    return parts + (0,) * (3 - len(parts))


def read_url(url, limit):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'api.github.com', 'github.com'}:
        raise ValueError('更新地址不受信任')
    request = Request(url, headers={'User-Agent': 'ServiceProcessWorkbench', 'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('更新文件超过大小限制')
    return data


def parse_release(data, current):
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('不能使用预发布版本')
    tag = data['tag_name']
    if version_tuple(tag) <= version_tuple(current):
        return None
    assets = {item['name']: item['browser_download_url'] for item in data.get('assets', [])}
    if ASSET_NAME not in assets or 'SHA256SUMS.txt' not in assets:
        raise ValueError('新版本缺少 EXE 或校验文件')
    prefix = f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
    for name in (ASSET_NAME, 'SHA256SUMS.txt'):
        if not assets[name].startswith(prefix):
            raise ValueError('更新文件来源不匹配')
    return {'version': tag, 'notes': str(data.get('body') or '')[:4000], 'assets': assets}


def latest_release(current):
    data = json.loads(read_url(f'https://api.github.com/repos/{REPOSITORY}/releases/latest', 1024 * 1024))
    return parse_release(data, current)


def download_release(release, directory):
    sums = read_url(release['assets']['SHA256SUMS.txt'], 16384).decode('utf-8')
    expected = None
    for line in sums.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('*') == ASSET_NAME:
            expected = fields[0].lower()
    if expected is None or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('校验文件格式错误')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ASSET_NAME
    temporary = directory / (ASSET_NAME + '.part')
    digest = hashlib.sha256()
    request = Request(release['assets'][ASSET_NAME], headers={'User-Agent': 'ServiceProcessWorkbench'})
    try:
        with urlopen(request, timeout=30) as response, temporary.open('wb') as output:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise ValueError('更新文件超过大小限制')
                digest.update(chunk)
                output.write(chunk)
        with temporary.open('rb') as executable:
            signature = executable.read(2)
        if digest.hexdigest() != expected or signature != b'MZ':
            raise ValueError('EXE 校验失败，未替换当前版本')
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def launch_replacement(target, source, pid, environment):
    target, source = Path(target).resolve(), Path(source).resolve()
    # Staging beside the target allows replacement without cross-volume moves.
    script = source.parent / 'replace.ps1'
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$target = {ps_quote(target)}\n$source = {ps_quote(source)}\n"
        "$backup = $target + '.previous'\n$staged = $target + '.update'\n"
        f"Wait-Process -Id {int(pid)} -Timeout 120 -ErrorAction SilentlyContinue\n"
        f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ exit 1 }}\n"
        "try {\n"
        " Copy-Item -LiteralPath $source -Destination $staged -Force\n"
        " Copy-Item -LiteralPath $target -Destination $backup -Force\n"
        " for ($attempt = 0; ; $attempt++) {\n"
        "  try { Move-Item -LiteralPath $staged -Destination $target -Force; break }\n"
        "  catch { if ($attempt -ge 29) { throw }; Start-Sleep -Seconds 1 }\n"
        " }\n"
        " Start-Process -FilePath $target -WorkingDirectory (Split-Path $target)\n"
        "} catch {\n"
        " $_ | Out-File -LiteralPath ($source + '.error.txt')\n"
        " if (Test-Path -LiteralPath $backup) {\n"
        "  Copy-Item -LiteralPath $backup -Destination $target -Force\n"
        "  Start-Process -FilePath $target -WorkingDirectory (Split-Path $target)\n"
        " }\n"
        "} finally { Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue }\n",
        encoding='utf-8-sig')
    return subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', str(script)],
        env=environment, creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
