import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench_core import (
    BoundedLogQueue,
    ConfigStore,
    append_rotating_log,
    build_start_schedule,
    normalize_config_data,
    resolve_data_dir,
)


class ConfigStoreTests(unittest.TestCase):
    def test_resolve_data_dir_uses_local_appdata_independent_of_exe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp_dir}):
                self.assertEqual(
                    resolve_data_dir(),
                    Path(temp_dir) / "ServiceProcessWorkbench",
                )

    def test_load_migrates_legacy_config_and_preserves_alias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "appdata"
            legacy = root / "old" / "manager_config.json"
            legacy.parent.mkdir()
            legacy.write_text(
                json.dumps(
                    {
                        "tabs": [
                            {
                                "name": "北京接口服务",
                                "service_type": "jar",
                                "exe_path": "D:/apps/beijing.jar",
                                "auto_start_instance": True,
                            }
                        ],
                        "sys_auto_start": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = ConfigStore(data_dir, [legacy]).load()

            self.assertEqual(result.data["tabs"][0]["name"], "北京接口服务")
            self.assertTrue(result.data["tabs"][0]["id"])
            self.assertTrue(result.data["sys_auto_start"])
            self.assertIn("已导入旧版配置", result.warning)
            self.assertTrue((data_dir / "manager_config.json").exists())

    def test_save_and_reload_retains_service_id_and_alias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir))
            first = normalize_config_data(
                {"tabs": [{"name": "交易中心采集", "exe_path": "D:/collector.exe"}]}
            )
            service_id = first["tabs"][0]["id"]

            store.save(first)
            reloaded = store.load().data

            self.assertEqual(reloaded["tabs"][0]["id"], service_id)
            self.assertEqual(reloaded["tabs"][0]["name"], "交易中心采集")

    def test_corrupt_primary_recovers_previous_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConfigStore(Path(temp_dir))
            first = normalize_config_data({"tabs": [{"name": "第一版"}]})
            second = normalize_config_data({"tabs": [{"name": "第二版"}]})
            store.save(first)
            store.save(second)
            store.path.write_text("{broken", encoding="utf-8")

            result = store.load()

            self.assertEqual(result.data["tabs"][0]["name"], "第一版")
            self.assertIn("备份", result.warning)

    def test_normalize_ignores_unknown_fields_and_invalid_tabs(self):
        data = normalize_config_data(
            {
                "tabs": [
                    None,
                    {"name": "有效服务", "unknown": 123, "service_type": "bad"},
                ],
                "unknown": "ignored",
            }
        )

        self.assertEqual(len(data["tabs"]), 1)
        self.assertEqual(data["tabs"][0]["service_type"], "exe")
        self.assertNotIn("unknown", data["tabs"][0])


class LoggingTests(unittest.TestCase):
    def test_bounded_queue_reports_dropped_messages(self):
        messages = BoundedLogQueue(max_messages=3)
        for value in ["a", "b", "c", "d", "e"]:
            messages.put(value)

        drained = messages.drain(10)

        self.assertIn("丢弃 2 条", drained[0])
        self.assertEqual(drained[1:], ["a", "b", "c"])
        self.assertEqual(messages.size, 0)

    def test_drain_limit_leaves_remaining_messages_bounded(self):
        messages = BoundedLogQueue(max_messages=5)
        for value in ["a", "b", "c"]:
            messages.put(value)

        self.assertEqual(messages.drain(2), ["a", "b"])
        self.assertEqual(messages.size, 1)

    def test_clear_removes_messages_and_overflow_notice(self):
        messages = BoundedLogQueue(max_messages=1)
        messages.put("kept")
        messages.put("dropped")

        messages.clear()

        self.assertEqual(messages.drain(10), [])

    def test_rotating_log_retains_requested_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "service.log"
            append_rotating_log(path, "123456", max_bytes=10, backups=2)
            append_rotating_log(path, "abcdef", max_bytes=10, backups=2)
            append_rotating_log(path, "UVWXYZ", max_bytes=10, backups=2)

            self.assertEqual(path.read_text(encoding="utf-8"), "UVWXYZ")
            self.assertEqual(path.with_suffix(".log.1").read_text(encoding="utf-8"), "abcdef")
            self.assertEqual(path.with_suffix(".log.2").read_text(encoding="utf-8"), "123456")


class StartupScheduleTests(unittest.TestCase):
    def test_empty_schedule_has_no_callbacks(self):
        self.assertEqual(build_start_schedule(0), [])

    def test_services_start_after_initial_delay_and_in_order(self):
        self.assertEqual(build_start_schedule(3), [5000, 7000, 9000])


if __name__ == "__main__":
    unittest.main()
