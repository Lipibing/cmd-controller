import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workbench_update as update


class UpdateTests(unittest.TestCase):
    def release(self):
        prefix = 'https://github.com/Lipibing/cmd-controller/releases/download/v2.8/'
        return {'tag_name': 'v2.8', 'assets': [
            {'name': name, 'browser_download_url': prefix + name}
            for name in (update.ASSET_NAME, 'SHA256SUMS.txt')]}

    def test_version_comparison(self):
        self.assertGreater(update.version_tuple('v2.10'), update.version_tuple('v2.9'))
        self.assertEqual(update.version_tuple('2.8'), update.version_tuple('v2.8.0'))
        with self.assertRaises(ValueError):
            update.version_tuple('v2.8-beta')

    def test_release_and_no_update(self):
        self.assertEqual(update.parse_release(self.release(), 'v2.7')['version'], 'v2.8')
        self.assertIsNone(update.parse_release(self.release(), 'v2.8'))

    def test_reject_missing_asset_and_wrong_source(self):
        data = self.release()
        data['assets'][0]['browser_download_url'] = 'https://example.com/a.exe'
        with self.assertRaises(ValueError):
            update.parse_release(data, 'v2.7')
        data['assets'] = []
        with self.assertRaises(ValueError):
            update.parse_release(data, 'v2.7')

    def test_download_verified_and_mismatch_cleanup(self):
        import io
        data = b'MZ' + b'example'
        release = update.parse_release(self.release(), 'v2.7')
        with tempfile.TemporaryDirectory() as directory:
            for valid in (True, False):
                digest = hashlib.sha256(data).hexdigest() if valid else '0' * 64
                sums = f'{digest}  {update.ASSET_NAME}'.encode()
                with patch.object(update, 'read_url', return_value=sums), patch.object(update, 'urlopen', return_value=io.BytesIO(data)):
                    if valid:
                        self.assertEqual(update.download_release(release, directory).read_bytes(), data)
                    else:
                        with self.assertRaises(ValueError):
                            update.download_release(release, directory)
                self.assertFalse((Path(directory) / (update.ASSET_NAME + '.part')).exists())
            self.assertEqual((Path(directory) / update.ASSET_NAME).read_bytes(), data)

    def test_powershell_quote(self):
        self.assertEqual(update.ps_quote("C:/a'b.exe"), "'C:/a''b.exe'")

    def test_timeout_retries_and_explains_connectivity(self):
        from urllib.error import URLError
        with patch.object(update, 'urlopen', side_effect=URLError(TimeoutError('timed out'))) as opener, \
                patch.object(update.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '网络'):
                update.read_url('https://api.github.com/test', 100)
        self.assertEqual(opener.call_count, 3)

    def test_invalid_proxy_is_rejected(self):
        with self.assertRaises(ValueError):
            update.read_url('https://api.github.com/test', 100, proxy='file:///tmp/a')

    def test_direct_mode_does_not_use_system_proxy(self):
        with patch.object(update, 'ProxyHandler') as handler, patch.object(update, 'build_opener') as opener:
            update.open_request('https://api.github.com/test', 'direct')
        handler.assert_called_once_with({})
        opener.return_value.open.assert_called_once()

    def test_helper_waits_backs_up_and_uses_isolated_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'new.exe'
            target = Path(directory) / 'old.exe'
            with patch.object(update.subprocess, 'Popen') as launch:
                update.launch_replacement(target, source, 1234, {'PYINSTALLER_RESET_ENVIRONMENT': '1'})
            script = (Path(directory) / 'replace.ps1').read_text(encoding='utf-8-sig')
            self.assertIn('Wait-Process -Id 1234', script)
            self.assertIn("$backup = $target + '.previous'", script)
            self.assertIn('Copy-Item -LiteralPath $backup -Destination $target', script)
            self.assertIn('$attempt -ge 29', script)
            self.assertEqual(launch.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
