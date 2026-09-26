"""app.py 的 Api 层测试。

重点覆盖文件对话框的错误处理：对话框失败必须能被界面区分出来，
否则表现成「点了按钮毫无反应」，没法排查。
运行：python -m unittest tests.test_app_api -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import core  # noqa: E402
import db  # noqa: E402


class FakeWindow:
    """替身窗口，可按需返回结果或抛异常。"""

    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def create_file_dialog(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc:
            raise self.exc
        return self.result


def make_api(window=None, gui=True, seed=3):
    conn = db.connect(":memory:")
    for i in range(1, seed + 1):
        core.add_student(conn, f"学生{i}", f"2024{i:03d}", "一班")
    api = appmod.Api(conn=conn)
    api._gui = gui
    api._window = window
    return api


class ValidatingFakeWindow:
    """模拟 pywebview：打开对话框前先校验过滤器，非法就抛 ValueError。

    这是真实失败路径的复现——原来过滤器带斜杠，
    create_file_dialog 在这一步就抛错，对话框根本没机会打开。
    """

    def __init__(self, result=None):
        self.result = result

    def create_file_dialog(self, dialog_type=10, directory="", allow_multiple=False,
                           save_filename="", file_types=()):
        from webview.util import parse_file_type

        for ft in file_types:
            parse_file_type(ft)
        return self.result


class TestImportFileDialog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.csv = os.path.join(self.dir, "名单.csv")
        Path(self.csv).write_text("姓名,学号,班级\n张三,2024001,一班\n", encoding="utf-8-sig")

    def tearDown(self):
        self.tmp.cleanup()

    def test_dialog_failure_is_reported_not_silently_cancelled(self):
        """对话框抛异常时，必须返回 error 而不是 cancelled。

        这是修过的 bug：原来所有异常都被吞掉当作取消，
        界面上表现成点了按钮没反应。
        """
        api = make_api(window=FakeWindow(exc=RuntimeError("COM 组件未注册")))
        result = api.import_file()
        self.assertIn("error", result, f"应返回错误，实际 {result}")
        self.assertNotIn("cancelled", result)
        self.assertIn("COM 组件未注册", result["error"])

    def test_filters_survive_pywebview_validation(self):
        """端到端复现：过滤器必须能通过 pywebview 校验，否则对话框打不开。"""
        try:
            import webview  # noqa: F401
        except ImportError:
            self.skipTest("未安装 pywebview")
        api = make_api(window=ValidatingFakeWindow(result=(self.csv,)))
        result = api.import_file()
        self.assertNotIn("error", result, f"过滤器应合法，实际报错：{result.get('error')}")
        self.assertEqual(result["added"], 1, result)

    def test_broken_filter_would_be_caught(self):
        """反证：换成带斜杠的旧过滤器，这条路径就会失败。"""
        try:
            import webview  # noqa: F401
        except ImportError:
            self.skipTest("未安装 pywebview")
        api = make_api(window=ValidatingFakeWindow(result=(self.csv,)))
        original = appmod.FILTER_GROUPS["表格导入"]
        appmod.FILTER_GROUPS["表格导入"] = ("Excel / CSV (*.xlsx;*.csv)",)
        try:
            result = api.import_file()
            self.assertIn("error", result, "非法过滤器应当被报错")
        finally:
            appmod.FILTER_GROUPS["表格导入"] = original

    def test_user_cancel_returns_cancelled(self):
        api = make_api(window=FakeWindow(result=None))
        self.assertEqual(api.import_file(), {"cancelled": True})

    def test_empty_selection_is_cancelled(self):
        api = make_api(window=FakeWindow(result=[]))
        self.assertEqual(api.import_file(), {"cancelled": True})

    def test_successful_import(self):
        api = make_api(window=FakeWindow(result=(self.csv,)))
        result = api.import_file()
        self.assertEqual(result["added"], 1, result)
        self.assertNotIn("error", result)
        names = [s["name"] for s in core.list_students(api._conn)]
        self.assertIn("张三", names)

    def test_result_as_plain_string(self):
        """pywebview 某些版本返回单字符串而不是列表。"""
        api = make_api(window=FakeWindow(result=self.csv))
        self.assertEqual(api.import_file()["added"], 1)

    def test_corrupt_xls_returns_error_not_crash(self):
        bad = os.path.join(self.dir, "损坏.xls")
        Path(bad).write_bytes(b"\xd0\xcf\x11\xe0")
        api = make_api(window=FakeWindow(result=(bad,)))
        result = api.import_file()
        self.assertIn("error", result)
        self.assertIn("读不了", result["error"])


class TestSaveFileDialog(unittest.TestCase):
    def test_save_dialog_failure_is_reported(self):
        api = make_api(window=FakeWindow(exc=OSError("没有权限")))
        result = api.export_csv()
        self.assertIn("error", result, f"应返回错误，实际 {result}")
        self.assertNotIn("cancelled", result)

    def test_save_dialog_cancel(self):
        api = make_api(window=FakeWindow(result=None))
        self.assertEqual(api.export_csv(), {"cancelled": True})

    def test_export_writes_file_with_bom(self):
        target = Path(tempfile.mkdtemp()) / "记录.csv"
        api = make_api(window=FakeWindow(result=str(target)))
        core.draw(api._conn)
        result = api.export_csv()
        self.assertEqual(result.get("path"), str(target))
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "导出文件应带 UTF-8 BOM")

    def test_export_without_dialog_uses_export_folder(self):
        """HTTP 外壳下没有对话框，应落到程序目录的「导出」文件夹。"""
        api = make_api(window=None, gui=False)
        result = api.export_csv()
        self.assertIn("path", result, f"应给出落盘路径，实际 {result}")
        self.assertTrue(Path(result["path"]).exists())
        Path(result["path"]).unlink()


class TestFileFilters(unittest.TestCase):
    """文件过滤器格式必须符合 pywebview 的校验。

    这个 bug 真实发生过：描述写成 "Excel / CSV (*.xlsx;*.csv)"（含斜杠）
    被 pywebview 判为非法过滤器，create_file_dialog 直接抛 ValueError，
    对话框压根打不开。改过滤器字符串必须跑这里。
    """

    def setUp(self):
        try:
            from webview.util import parse_file_type  # noqa: F401
        except ImportError:
            self.skipTest("未安装 pywebview")

    def test_all_filters_pass_pywebview_parser(self):
        from webview.util import parse_file_type

        checked = 0
        for group, filters in appmod.FILTER_GROUPS.items():
            for f in filters:
                with self.subTest(组=group, 过滤器=f):
                    try:
                        desc, exts = parse_file_type(f)
                    except ValueError as exc:
                        self.fail(f"{group} 的过滤器非法：{f} —— {exc}")
                    self.assertTrue(desc.strip(), "描述不能为空")
                    self.assertTrue(exts.strip(), "扩展名不能为空")
                    checked += 1
        self.assertGreaterEqual(checked, 4, "过滤器数量少于预期")

    def test_slash_in_description_is_rejected(self):
        """留个反例样本，说明为什么不能用斜杠，防止以后改回去。"""
        from webview.util import parse_file_type

        with self.assertRaises(ValueError):
            parse_file_type("Excel / CSV (*.xlsx;*.csv)")

    def test_table_filter_covers_xlsx_and_csv(self):
        """导入表格必须同时允许 .xlsx 和 .csv。"""
        from webview.util import parse_file_type

        _, exts = parse_file_type(appmod.FILTER_TABLE)
        self.assertIn("*.xlsx", exts)
        self.assertIn("*.csv", exts)

    def test_backup_filter_is_json(self):
        from webview.util import parse_file_type

        _, exts = parse_file_type(appmod.FILTER_BACKUP)
        self.assertIn("*.json", exts)


class TestApiMisc(unittest.TestCase):
    def test_update_student_rejects_unknown_field(self):
        api = make_api()
        sid = core.list_students(api._conn)[0]["id"]
        with self.assertRaises(Exception) as ctx:
            api.update_student(sid, "drawn", "1")
        self.assertIn("不支持的字段", str(ctx.exception))

    def test_update_student_single_field(self):
        api = make_api()
        sid = core.list_students(api._conn)[0]["id"]
        api.update_student(sid, "name", "新名字")
        names = [s["name"] for s in core.list_students(api._conn)]
        self.assertIn("新名字", names)

    def test_undo_bad_id_raises_plain_exception(self):
        """pywebview 只能把异常信息传给界面，故需转成普通 Exception。"""
        api = make_api()
        with self.assertRaises(Exception) as ctx:
            api.undo(99999)
        self.assertIn("已不存在", str(ctx.exception))

    def test_state_shape(self):
        api = make_api()
        st = api.state()
        for key in ("stats", "students", "history", "rounds", "poolNames", "dataDir"):
            self.assertIn(key, st)
        self.assertEqual(st["stats"]["total"], 3)

    def test_add_student_empty_name_raises(self):
        api = make_api()
        with self.assertRaises(Exception):
            api.add_student("   ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
