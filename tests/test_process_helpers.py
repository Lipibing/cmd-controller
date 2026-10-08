import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os
from unittest.mock import MagicMock

from win_service_manager import ManagedProcess, ProgramConfig, _parse_process_rows


class _AppConfig:
    auto_restart_on_crash = False


class _App:
    config_obj = _AppConfig()
    _closing = False


class ManagedProcessTests(unittest.TestCase):
    def test_autostart_check_rejects_old_executable_path(self):
        from win_service_manager import AutoStartManager
        with patch('winreg.OpenKey'), \
                patch('winreg.QueryValueEx', return_value=('"D:/old/workbench.exe"', 1)):
            valid, command = AutoStartManager.inspect()
        self.assertFalse(valid)
        self.assertIn('old', command)

    def test_autostart_check_accepts_current_command(self):
        from win_service_manager import AutoStartManager
        with patch('winreg.OpenKey'), \
                patch('winreg.QueryValueEx', return_value=(AutoStartManager.expected_command(), 1)):
            valid, _ = AutoStartManager.inspect()
        self.assertTrue(valid)

    def test_exe_and_jar_launch_without_parent_extraction_environment(self):
        inherited = {
            '_MEIPASS2': 'parent-temp',
            '_PYI_APPLICATION_HOME_DIR': 'parent-temp',
            '_PYI_ARCHIVE_FILE': 'workbench.exe',
            '_PYI_PARENT_PROCESS_LEVEL': '1',
            'PATH': 'business-path',
            'BUSINESS_TOKEN': 'test-value',
        }
        for kind in ('exe', 'jar'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / ('service.' + kind)
                target.touch()
                process = ManagedProcess(
                    ProgramConfig(service_type=kind, exe_path=str(target)),
                    lambda _: None, _App(),
                )
                with patch.dict(os.environ, inherited, clear=True), \
                        patch('win_service_manager._query_running_exe_pids', return_value=[]), \
                        patch('win_service_manager._query_running_jar_pids', return_value=[]), \
                        patch('win_service_manager.subprocess.Popen', return_value=MagicMock(pid=123)) as launch, \
                        patch('win_service_manager.threading.Thread'):
                    process.start()
                    env = launch.call_args.kwargs['env']
                    self.assertEqual(env, {'PATH': 'business-path', 'BUSINESS_TOKEN': 'test-value',
                                           'PYINSTALLER_RESET_ENVIRONMENT': '1'})
                    self.assertEqual(os.environ['_MEIPASS2'], 'parent-temp')
                    self.assertEqual(launch.call_args.kwargs['cwd'], str(target.parent))

    def test_process_rows_include_full_ancestor_chain(self):
        rows = _parse_process_rows("200|999|999,100,1\n")
        self.assertEqual(rows[0]["ancestors"], [999, 100, 1])

    def test_same_exe_descendant_through_intermediate_process_is_not_external(self):
        process = ManagedProcess(
            ProgramConfig(service_type="exe", exe_path="D:/test.exe"),
            lambda _message: None,
            _App(),
        )
        process.proc = type("Proc", (), {"pid": 100, "poll": lambda self: None})()
        rows = [
            {"pid": 100, "parent_pid": 1, "ancestors": [1]},
            {"pid": 200, "parent_pid": 999, "ancestors": [999, 100, 1]},
            {"pid": 300, "parent_pid": 998, "ancestors": [998, 1]},
        ]

        with patch("win_service_manager._query_running_exe_processes", return_value=rows):
            duplicates = process.external_duplicate_pids()

        self.assertEqual(duplicates, [300])

    def test_duplicate_query_survives_concurrent_stop(self):
        process = ManagedProcess(
            ProgramConfig(service_type="exe", exe_path="D:/test.exe"),
            lambda _message: None,
            _App(),
        )
        process.proc = type("Proc", (), {"pid": 100, "poll": lambda self: None})()

        def finish_after_stop(_path):
            process.proc = None
            return [
                {"pid": 100, "parent_pid": 1, "ancestors": [1]},
                {"pid": 300, "parent_pid": 1, "ancestors": [1]},
            ]

        with patch("win_service_manager._query_running_exe_processes", side_effect=finish_after_stop):
            duplicates = process.external_duplicate_pids()

        self.assertEqual(duplicates, [300])

    def test_missing_jar_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.jar"
            process = ManagedProcess(
                ProgramConfig(service_type="jar", exe_path=str(missing)),
                lambda _message: None,
                _App(),
            )

            with self.assertRaisesRegex(FileNotFoundError, "JAR"):
                process.start()

    def test_manual_stop_clears_pending_restart_request(self):
        process = ManagedProcess(ProgramConfig(), lambda _message: None, _App())
        process._restart_requested = True

        process.stop()

        self.assertFalse(process.consume_restart_request())


if __name__ == "__main__":
    unittest.main()
