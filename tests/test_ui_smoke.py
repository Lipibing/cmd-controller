import tempfile
import tkinter as tk
import unittest
import time
import hashlib
from tkinter import ttk
from pathlib import Path
from unittest.mock import patch

import win_service_manager as wsm
from workbench_core import ConfigStore, normalize_config_data


class AppUiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.config_data = normalize_config_data(
            {
                "tabs": [
                    {"name": "服务一", "exe_path": "D:/missing-one.exe"},
                    {"name": "服务二", "exe_path": "D:/missing-two.exe"},
                    {"name": "服务三", "exe_path": "D:/missing-three.exe"},
                ]
            }
        )
        ConfigStore(self.data_dir).save(self.config_data)
        self.legacy_base = self.data_dir / "legacy"
        legacy_log_dir = self.legacy_base / "logs"
        legacy_log_dir.mkdir(parents=True)
        first = self.config_data["tabs"][0]
        key = f"{first['service_type']}|{first['exe_path']}|{first['name']}"
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        legacy_log = legacy_log_dir / f"{wsm._safe_file_stem(first['name'])}_{digest}.log"
        legacy_log.write_text("v2.2 legacy history\n", encoding="utf-8")
        self.patches = [
            patch.object(wsm, "DATA_DIR", self.data_dir),
            patch.object(wsm, "CONFIG_PATH", self.data_dir / "manager_config.json"),
            patch.object(wsm, "LOG_DIR", self.data_dir / "logs"),
            patch.object(wsm, "BASE_DIR", self.legacy_base),
        ]
        for item in self.patches:
            item.start()
        self.root = wsm.ctk.CTk()
        self.root.withdraw()
        self.app = wsm.App(self.root)
        self.root.update()

    def tearDown(self):
        self.app.dispose(stop_process=False)
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()
        if self.root in wsm.ctk.AppearanceModeTracker.app_list:
            wsm.ctk.AppearanceModeTracker.app_list.remove(self.root)
        wsm.ctk.AppearanceModeTracker.update_loop_running = False
        wsm.ctk.ScalingTracker.update_loop_running = False
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_alias_is_saved_without_closing_application(self):
        self.app.tabs[0].name_var.set("新的远程服务别名")
        self.app.schedule_save(immediate=True)
        self.root.update()

        saved = ConfigStore(self.data_dir).load().data
        self.assertEqual(saved["tabs"][0]["name"], "新的远程服务别名")

    def test_legacy_log_is_migrated_to_stable_service_id(self):
        content = self.app.tabs[0].log_text.get("1.0", "end-1c")
        stable_log = self.data_dir / "logs" / f"service_{self.config_data['tabs'][0]['id']}.log"

        self.assertIn("v2.2 legacy history", content)
        self.assertTrue(stable_log.exists())

    def test_alias_typing_debounces_sidebar_rebuild(self):
        tab = self.app.tabs[0]
        with patch.object(tab, "refresh_cb", wraps=tab.refresh_cb) as render:
            for value in ["北", "北京", "北京接", "北京接口", "北京接口服务"]:
                tab.name_var.set(value)
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                self.root.update()

        self.assertEqual(render.call_count, 1)

    def test_deleting_non_current_tab_keeps_one_selected_frame(self):
        self.app.select_tab(2)
        with patch.object(wsm.messagebox, "askyesno", return_value=True):
            self.app.delete_tab(1)
        self.root.update()

        self.assertEqual(self.app.current_idx, 1)
        managed = [tab.frame.winfo_manager() for tab in self.app.tabs]
        self.assertEqual(managed.count("pack"), 1)

    def test_runtime_log_widget_is_trimmed(self):
        tab = self.app.tabs[0]
        tab.log_text.config(state="normal")
        tab.log_text.insert("end", "line\n" * (wsm.MAX_UI_LOG_LINES + 100))
        tab.log_text.insert("end", "x" * (wsm.MAX_UI_LOG_CHARS + 1000))
        tab._trim_log_widget()

        chars = tab.log_text.count("1.0", "end-1c", "chars")[0]
        self.assertLessEqual(chars, wsm.MAX_UI_LOG_CHARS)

    def test_log_scrollbar_style_has_no_arrow_buttons(self):
        layout = str(ttk.Style(self.root).layout("Log.Vertical.TScrollbar")).lower()
        self.assertNotIn("arrow", layout)
        self.assertIn("thumb", layout)

    def test_new_logs_do_not_steal_manual_scroll_position(self):
        self.root.deiconify()
        tab = self.app.tabs[0]
        tab.log_text.config(state="normal")
        tab.log_text.insert("end", "history\n" * 300)
        tab.log_text.config(state="disabled")
        self.root.update_idletasks()
        tab.log_text.yview_moveto(0.0)
        tab._sync_follow_state()
        before = tab.log_text.yview()[0]

        tab.log_queue.put("new output\n")
        tab._flush_log_queue()

        self.assertFalse(tab._follow_latest)
        self.assertAlmostEqual(tab.log_text.yview()[0], before, places=2)

    def test_show_latest_restores_follow_mode(self):
        self.root.deiconify()
        tab = self.app.tabs[0]
        tab.log_text.config(state="normal")
        tab.log_text.insert("end", "history\n" * 300)
        tab.log_text.config(state="disabled")
        self.root.update_idletasks()
        tab.log_text.yview_moveto(0.0)

        tab.show_latest()

        self.assertTrue(tab._follow_latest)
        self.assertAlmostEqual(tab.log_text.yview()[1], 1.0, places=3)

    def test_switching_service_shows_latest_log(self):
        self.root.deiconify()
        target = self.app.tabs[1]
        target.log_text.config(state="normal")
        target.log_text.insert("end", "line\n" * 300)
        target.log_text.config(state="disabled")
        self.root.update_idletasks()
        target.log_text.yview_moveto(0.0)
        target._sync_follow_state()

        self.app.select_tab(1)
        self.root.update()

        self.assertTrue(target._follow_latest)
        self.assertAlmostEqual(target.log_text.yview()[1], 1.0, places=3)

    def test_search_result_pauses_latest_follow(self):
        self.root.deiconify()
        tab = self.app.tabs[0]
        tab.log_text.config(state="normal")
        tab.log_text.insert("end", "needle\n" + "line\n" * 300)
        tab.log_text.config(state="disabled")
        tab.search_matches = [("1.0", "1.6")]
        tab.current_search_idx = 0
        tab._follow_latest = True

        tab._update_search_ui()

        self.assertFalse(tab._follow_latest)

    def test_log_flush_uses_small_ui_batch(self):
        tab = self.app.tabs[0]

        with patch.object(tab.log_queue, "drain", return_value=[]) as drain:
            tab._flush_log_queue()

        drain.assert_called_once_with(wsm.LOG_UI_BATCH_SIZE)
        self.assertEqual(wsm.LOG_UI_BATCH_SIZE, 500)

    def test_cancel_start_all_preserves_pending_tasks(self):
        before = list(self.app._startup_after_ids)
        with patch.object(wsm.messagebox, 'askyesno', return_value=False), \
                patch.object(self.app, '_schedule_start_tabs') as schedule:
            self.app.start_all()
        self.assertEqual(self.app._startup_after_ids, before)
        schedule.assert_not_called()

    def test_routine_buttons_execute_without_confirmation(self):
        for text in ('浏览', '搜索', '导出日志', '内存模板', '新增服务',
                     '系统设置', '检查开机启动', '检查更新'):
            with self.subTest(button=text):
                calls = []
                button = wsm.make_button(self.root, text, lambda: calls.append(True))
                try:
                    with patch.object(wsm.messagebox, 'askyesno', return_value=False) as confirm:
                        button.invoke()
                    confirm.assert_not_called()
                    self.assertEqual(calls, [True])
                finally:
                    button.destroy()

    def test_important_button_keeps_its_own_single_confirmation(self):
        button = wsm.make_button(self.root, '停止全部', self.app.stop_all)
        try:
            with patch.object(wsm.messagebox, 'askyesno', return_value=False) as confirm, \
                    patch.object(self.app.tabs[0].process, 'stop') as stop:
                button.invoke()
            confirm.assert_called_once()
            stop.assert_not_called()
        finally:
            button.destroy()

    def test_cancel_stop_all_does_not_stop_services(self):
        with patch.object(wsm.messagebox, 'askyesno', return_value=False), \
                patch.object(self.app.tabs[0].process, 'stop') as stop:
            self.app.stop_all()
        stop.assert_not_called()

    def test_cancel_clear_logs_preserves_history(self):
        tab = self.app.tabs[0]
        before = tab.log_text.get('1.0', 'end-1c')
        with patch.object(wsm.messagebox, 'askyesno', return_value=False):
            tab.clear_logs()
        self.assertEqual(tab.log_text.get('1.0', 'end-1c'), before)

    def test_accepted_stop_all_prompts_only_once(self):
        with patch.object(wsm.messagebox, 'askyesno', return_value=True) as confirm:
            self.app.stop_all()
        self.assertEqual(confirm.call_count, 1)

    def test_sidebar_resize_events_are_coalesced(self):
        self.root.update_idletasks()
        for _ in range(20):
            self.app._queue_sidebar_layout()
        pending = self.app._sidebar_layout_after
        self.assertIsNotNone(pending)
        self.app._queue_sidebar_layout()
        self.assertEqual(pending, self.app._sidebar_layout_after)

    def test_jar_fields_stay_above_actions_after_type_switch(self):
        self.root.deiconify()
        self.root.geometry('960x640')
        tab = self.app.tabs[0]
        for _ in range(3):
            tab.type_var.set('exe')
            tab._on_type_changed()
            tab.type_var.set('jar')
            tab._on_type_changed()
        self.root.update_idletasks()
        siblings = tab.action_row.master.pack_slaves()
        self.assertLess(siblings.index(tab.java_row), siblings.index(tab.jvm_row))
        self.assertLess(siblings.index(tab.jvm_row), siblings.index(tab.action_row))
        for widget in (tab.jvm_entry, tab.btn_jar_template):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                 self.root.winfo_rooty() + self.root.winfo_height())
        self.assertEqual(tab.btn_jar_template.master, tab.jvm_row)

    def test_form_fields_share_baselines_and_equal_columns(self):
        self.root.deiconify()
        for geometry in ('960x640', '1240x820'):
            self.root.geometry(geometry)
            self.root.update()
            tab = self.app.tabs[0]
            self.assertEqual(tab.name_entry.winfo_rooty(), tab.args_entry.winfo_rooty())
            self.assertLessEqual(abs(tab.name_entry.winfo_width() - tab.args_entry.winfo_width()), 1)
            self.assertEqual(tab.name_entry.winfo_height(), tab.args_entry.winfo_height())


if __name__ == "__main__":
    unittest.main()
