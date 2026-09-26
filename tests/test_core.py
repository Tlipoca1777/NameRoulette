"""核心逻辑测试。

重点覆盖那些一旦出错就会在课堂现场出问题的行为：
抽满一轮不重复、抽中即移出、撤回、重置不误伤停用学生、空池不崩。
运行：python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402
import db  # noqa: E402


def fresh_conn():
    conn = db.connect(":memory:")
    return conn


def seed(conn, n=10, cls="一班"):
    for i in range(1, n + 1):
        core.add_student(conn, f"学生{i:03d}", f"2024{i:04d}", cls)
    return conn


class TestDraw(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()

    def test_pool_counts(self):
        seed(self.conn, 200)
        s = core.stats(self.conn)
        self.assertEqual(s["total"], 200)
        self.assertEqual(s["remaining"], 200)
        self.assertEqual(s["drawn"], 0)

    def test_draw_removes_from_pool(self):
        seed(self.conn, 5)
        first = core.draw(self.conn)
        self.assertIsNotNone(first)
        s = core.stats(self.conn)
        self.assertEqual(s["remaining"], 4)
        self.assertEqual(s["drawn"], 1)

        pool = core.pool_names(self.conn)
        self.assertNotIn(first["name"], pool,
                         "抽中的学生必须立即从抽取池消失")

    def test_full_round_has_no_repeats(self):
        """抽满 200 次，必须覆盖每个人且不重复。"""
        seed(self.conn, 200)
        seen = [core.draw(self.conn)["name"] for _ in range(200)]
        self.assertEqual(len(set(seen)), 200, "同一人不得被抽中两次")
        self.assertEqual(core.stats(self.conn)["remaining"], 0)

    def test_draw_on_empty_pool_returns_none(self):
        seed(self.conn, 3)
        for _ in range(3):
            self.assertIsNotNone(core.draw(self.conn))
        self.assertIsNone(core.draw(self.conn), "池子空时应返回 None 而不是抛异常")
        self.assertEqual(core.stats(self.conn)["remaining"], 0)

    def test_draw_no_students_at_all(self):
        self.assertIsNone(core.draw(self.conn))

    def test_distribution_covers_all_students(self):
        """多轮随机后每个人都应被抽到过，验证不会漏掉人。"""
        seed(self.conn, 50)
        hit = set()
        for _ in range(10):
            while True:
                r = core.draw(self.conn)
                if r is None:
                    break
                hit.add(r["name"])
            core.start_new_term(self.conn)
        self.assertEqual(len(hit), 50)


class TestUndo(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        seed(self.conn, 10)

    def test_undo_restores_to_pool(self):
        d = core.draw(self.conn)
        res = core.undo(self.conn, d["draw_id"])
        self.assertTrue(res["restored"])
        self.assertIn(d["name"], core.pool_names(self.conn))
        self.assertEqual(core.stats(self.conn)["remaining"], 10)
        self.assertEqual(len(core.history(self.conn)), 0)

    def test_undo_renumbers_remaining(self):
        """撤回中间一条后，序号应重排为 1..n 连续，不留空洞。"""
        ids = [core.draw(self.conn)["draw_id"] for _ in range(5)]
        core.undo(self.conn, ids[2])
        seqs = [h["seq"] for h in core.history(self.conn)]
        self.assertEqual(seqs, [1, 2, 3, 4], f"序号应连续，实际 {seqs}")

    def test_undo_twice_raises(self):
        d = core.draw(self.conn)
        core.undo(self.conn, d["draw_id"])
        with self.assertRaises(ValueError):
            core.undo(self.conn, d["draw_id"])

    def test_undo_after_permanent_delete_does_not_crash(self):
        d = core.draw(self.conn)
        core.delete_student(self.conn, d["student_id"])
        res = core.undo(self.conn, d["draw_id"])
        self.assertFalse(res["restored"], "学生已被永久删除，撤回无法回池但不应崩溃")


class TestReset(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()

    def test_reset_restores_all_and_keeps_history(self):
        seed(self.conn, 10)
        for _ in range(4):
            core.draw(self.conn)
        res = core.start_new_term(self.conn)
        self.assertEqual(res["restored"], 4)
        self.assertEqual(res["new_round"], 2)
        self.assertEqual(core.stats(self.conn)["remaining"], 10)
        self.assertEqual(len(core.history(self.conn)), 4, "开始新学期不应清空历史")

    def test_reset_does_not_revive_disabled_students(self):
        """关键：请假/转走被停用的学生，不能因为开始新学期而回到池子。"""
        seed(self.conn, 10)
        target = core.list_students(self.conn)[0]
        core.set_enabled(self.conn, target["id"], False)

        core.draw(self.conn)
        core.start_new_term(self.conn)

        self.assertNotIn(target["name"], core.pool_names(self.conn),
                         "停用的学生不得在重置后回到抽取池")
        s = core.stats(self.conn)
        self.assertEqual(s["remaining"], 9)
        self.assertEqual(s["disabled"], 1)

    def test_reset_keeps_previous_round_history_queryable(self):
        seed(self.conn, 5)
        core.draw(self.conn)
        core.start_new_term(self.conn)
        core.draw(self.conn)
        self.assertEqual(len(core.history(self.conn, round=1)), 1)
        self.assertEqual(len(core.history(self.conn, round=2)), 1)
        self.assertEqual(core.all_rounds(self.conn), [2, 1])

    def test_disabled_student_never_drawn(self):
        seed(self.conn, 5)
        disabled = core.list_students(self.conn)[2]
        core.set_enabled(self.conn, disabled["id"], False)
        for _ in range(20):
            while core.draw(self.conn):
                pass
            core.start_new_term(self.conn)
        for h in core.history(self.conn):
            self.assertNotEqual(h["name"], disabled["name"])


class TestStudents(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()

    def test_add_requires_name(self):
        with self.assertRaises(ValueError):
            core.add_student(self.conn, "   ")

    def test_update_and_delete(self):
        core.add_student(self.conn, "张三", "001", "一班")
        sid = core.list_students(self.conn)[0]["id"]
        core.update_student(self.conn, sid, name="张三丰")
        self.assertEqual(core.list_students(self.conn)[0]["name"], "张三丰")

        core.delete_student(self.conn, sid)
        self.assertEqual(len(core.list_students(self.conn)), 0)

    def test_history_survives_permanent_delete(self):
        """永久删除学生后，历史记录仍需保留姓名，不能变成空白行。"""
        core.add_student(self.conn, "李四", "002", "二班")
        d = core.draw(self.conn)
        core.delete_student(self.conn, d["student_id"])

        h = core.history(self.conn)
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["name"], "李四")
        self.assertEqual(h[0]["cls"], "二班")
        self.assertIsNone(h[0]["student_id"])


class TestCsvImport(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()

    def test_utf8_with_header(self):
        text = "姓名,学号,班级\n张三,2024001,一班\n李四,2024002,一班\n"
        res = core.import_students(self.conn, text)
        self.assertEqual(res["added"], 2)
        rows = core.list_students(self.conn)
        self.assertEqual(rows[0]["name"], "张三")
        self.assertEqual(rows[0]["sid"], "2024001")
        self.assertEqual(rows[0]["cls"], "一班")

    def test_header_order_is_irrelevant(self):
        text = "班级,姓名,学号\n一班,张三,2024001\n"
        core.import_students(self.conn, text)
        r = core.list_students(self.conn)[0]
        self.assertEqual((r["name"], r["sid"], r["cls"]), ("张三", "2024001", "一班"))

    def test_gbk_encoded_file(self):
        """Excel 在中文 Windows 上另存的 CSV 通常是 GBK。"""
        raw = "姓名,学号,班级\n王五,2024003,三班\n".encode("gbk")
        text = core.decode_bytes(raw)
        res = core.import_students(self.conn, text)
        self.assertEqual(res["added"], 1)
        self.assertEqual(core.list_students(self.conn)[0]["name"], "王五")

    def test_bom_encoded_file(self):
        raw = "\ufeff姓名,学号\n赵六,2024004\n".encode("utf-8")
        core.import_students(self.conn, core.decode_bytes(raw))
        r = core.list_students(self.conn)[0]
        self.assertEqual(r["name"], "赵六", "BOM 不应污染首个姓名")
        self.assertEqual(r["sid"], "2024004")

    def test_no_header_name_first(self):
        core.import_students(self.conn, "张三,2024001,一班\n李四,2024002,二班\n")
        r = core.list_students(self.conn)[0]
        self.assertEqual(r["name"], "张三")

    def test_no_header_id_first(self):
        """无表头时若首列是长数字，应识别为学号在前。"""
        core.import_students(self.conn, "2024001,张三,一班\n2024002,李四,一班\n")
        r = core.list_students(self.conn)[0]
        self.assertEqual(r["name"], "张三")
        self.assertEqual(r["sid"], "2024001")

    def test_single_column_paste(self):
        core.import_students(self.conn, "张三\n李四\n王五\n")
        self.assertEqual(len(core.list_students(self.conn)), 3)

    def test_single_column_with_numbering(self):
        core.import_students(self.conn, "1. 张三\n2. 李四\n3、王五\n")
        names = [r["name"] for r in core.list_students(self.conn)]
        self.assertEqual(sorted(names), sorted(["张三", "李四", "王五"]))

    def test_leading_digit_in_name_not_stripped(self):
        """姓名里以数字开头时不能被当成序号削掉首字。"""
        core.import_students(self.conn, "3班李四\n")
        self.assertEqual(core.list_students(self.conn)[0]["name"], "3班李四")

    def test_tab_separated(self):
        core.import_students(self.conn, "姓名\t学号\n张三\t2024001\n")
        self.assertEqual(core.list_students(self.conn)[0]["sid"], "2024001")

    def test_duplicate_import_is_skipped(self):
        text = "姓名,学号,班级\n张三,2024001,一班\n"
        core.import_students(self.conn, text)
        res = core.import_students(self.conn, text)
        self.assertEqual(res["added"], 0)
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(len(core.list_students(self.conn)), 1)

    def test_same_name_different_sid_is_kept_and_warned(self):
        """同名不同学号是真实存在的（同名同学），不能当重复丢掉。"""
        text = "姓名,学号\n张伟,2024001\n张伟,2024002\n"
        res = core.import_students(self.conn, text)
        self.assertEqual(res["added"], 2)
        self.assertEqual(len(core.list_students(self.conn)), 2)
        self.assertTrue(res["warnings"])

    def test_empty_input(self):
        self.assertEqual(core.import_students(self.conn, "")["added"], 0)
        self.assertEqual(core.import_students(self.conn, "\n\n  \n")["added"], 0)


class TestExcelImport(unittest.TestCase):
    """Excel 导入路径。需要 openpyxl，未安装则跳过。"""

    def setUp(self):
        self.conn = fresh_conn()
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("未安装 openpyxl")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def make_xlsx(self, sheets: dict, name="名单.xlsx"):
        from openpyxl import Workbook

        wb = Workbook()
        wb.remove(wb.active)
        for title, rows in sheets.items():
            ws = wb.create_sheet(title=title)
            for row in rows:
                ws.append(row)
        path = os.path.join(self.dir, name)
        wb.save(path)
        return path

    def test_basic_xlsx(self):
        path = self.make_xlsx({
            "Sheet1": [["姓名", "学号", "班级"],
                       ["张三", 2024240101, "计算机2401"],
                       ["李四", 2024240102, "计算机2401"]],
        })
        res = core.import_students_from_file(self.conn, path)
        self.assertEqual(res["added"], 2)
        rows = core.list_students(self.conn)
        self.assertEqual(rows[0]["name"], "张三")
        self.assertEqual(rows[0]["sid"], "2024240101", "数值学号不应带 .0 尾巴")
        self.assertEqual(rows[0]["cls"], "计算机2401")

    def test_title_row_above_header(self):
        """学校名单常在首行放标题，表头在第 2 行。"""
        path = self.make_xlsx({
            "Sheet1": [["计算机2401班学生名单", None, None],
                       ["姓名", "学号", "班级"],
                       ["王五", "2024240103", "计算机2401"]],
        })
        res = core.import_students_from_file(self.conn, path)
        self.assertEqual(res["added"], 1, f"应跳过标题行，结果：{res}")
        self.assertEqual(core.list_students(self.conn)[0]["name"], "王五")

    def test_picks_sheet_with_most_students(self):
        """第一张是封面页时应自动选到真正的名单表。"""
        path = self.make_xlsx({
            "封面": [["某某学院学生名册"], ["制表日期：2024-09-01"]],
            "名单": [["姓名", "学号"],
                     ["赵六", "2024240104"],
                     ["孙七", "2024240105"],
                     ["周八", "2024240106"]],
        })
        res = core.import_students_from_file(self.conn, path)
        self.assertEqual(res["added"], 3)
        self.assertIn("名单", res["source"])

    def test_corrupt_xls_gives_clear_error(self):
        path = os.path.join(self.dir, "损坏.xls")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0")
        with self.assertRaises(ValueError) as ctx:
            core.import_students_from_file(self.conn, path)
        self.assertIn("读不了", str(ctx.exception))

    def test_unsupported_extension(self):
        path = os.path.join(self.dir, "数据.pdf")
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4")
        with self.assertRaises(ValueError):
            core.import_students_from_file(self.conn, path)

    def test_empty_rows_ignored(self):
        path = self.make_xlsx({
            "Sheet1": [["姓名", "学号"], ["张三", "1"], [None, None], ["", ""],
                       ["李四", "2"]],
        })
        res = core.import_students_from_file(self.conn, path)
        self.assertEqual(res["added"], 2)


class TestCsvExport(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()

    def test_export_has_bom_and_content(self):
        seed(self.conn, 3)
        core.draw(self.conn)
        text = core.export_csv_text(self.conn)
        self.assertTrue(text.startswith("\ufeff"), "缺少 BOM，Excel 打开会乱码")
        lines = text.lstrip("\ufeff").strip().split("\r\n")
        self.assertEqual(lines[0], "序号,日期,时间,学期,姓名,学号,班级")
        self.assertEqual(len(lines), 2)
        self.assertIn("学生", lines[1])

    def test_export_empty_history(self):
        seed(self.conn, 3)
        text = core.export_csv_text(self.conn)
        self.assertIn("序号,日期,时间,学期,姓名,学号,班级", text)

    def test_export_filtered_by_round(self):
        seed(self.conn, 5)
        core.draw(self.conn)
        core.start_new_term(self.conn)
        core.draw(self.conn)
        core.draw(self.conn)
        self.assertEqual(len(core.export_csv_text(self.conn, round=1).strip().split("\r\n")), 2)
        self.assertEqual(len(core.export_csv_text(self.conn, round=2).strip().split("\r\n")), 3)


class TestBackup(unittest.TestCase):
    def test_roundtrip(self):
        src = fresh_conn()
        seed(src, 20)
        for _ in range(5):
            core.draw(src)
        backup = core.export_backup_text(src)

        dst = fresh_conn()
        core.import_backup_text(dst, backup)

        self.assertEqual(core.stats(src)["remaining"], core.stats(dst)["remaining"])
        self.assertEqual(core.stats(src)["total"], core.stats(dst)["total"])
        self.assertEqual(core.stats(src)["round"], core.stats(dst)["round"])
        self.assertEqual(len(core.history(src)), len(core.history(dst)))
        self.assertEqual(
            sorted(core.pool_names(src)), sorted(core.pool_names(dst))
        )

    def test_restore_preserves_disabled_flag(self):
        src = fresh_conn()
        seed(src, 5)
        core.set_enabled(src, core.list_students(src)[0]["id"], False)
        dst = fresh_conn()
        core.import_backup_text(dst, core.export_backup_text(src))
        self.assertEqual(core.stats(dst)["disabled"], 1)




class TestBatch(unittest.TestCase):
    """批量停用/启用/删除（名单管理的勾选功能）。"""

    def setUp(self):
        self.conn = fresh_conn()
        seed(self.conn, 5, "一班")

    def ids(self):
        return [s["id"] for s in core.list_students(self.conn)]

    def test_batch_disable_and_enable(self):
        ids = self.ids()
        self.assertEqual(core.set_enabled_many(self.conn, ids[:3], False)["updated"], 3)
        self.assertEqual(core.stats(self.conn)["disabled"], 3)
        self.assertEqual(core.stats(self.conn)["remaining"], 2)
        self.assertEqual(core.set_enabled_many(self.conn, ids[:3], True)["updated"], 3)
        self.assertEqual(core.stats(self.conn)["remaining"], 5)

    def test_batch_delete_keeps_history(self):
        ids = self.ids()
        d = core.draw(self.conn)
        self.assertEqual(core.delete_students(self.conn, ids[:2])["deleted"], 2)
        self.assertEqual(len(core.list_students(self.conn)), 3)
        h = core.history(self.conn)
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["name"], d["name"], "历史必须保留姓名快照")

    def test_empty_and_unknown_ids_are_safe(self):
        self.assertEqual(core.set_enabled_many(self.conn, [], False)["updated"], 0)
        self.assertEqual(core.delete_students(self.conn, [])["deleted"], 0)
        # 不存在的 id 不应报错，只影响 0 行
        self.assertEqual(core.set_enabled_many(self.conn, [9999], False)["updated"], 0)
        self.assertEqual(core.delete_students(self.conn, [9999])["deleted"], 0)

    def test_batch_disable_then_reset_does_not_revive(self):
        """批量停用的人，开始新学期也不能回到池子。"""
        ids = self.ids()
        core.set_enabled_many(self.conn, ids[:3], False)
        core.draw(self.conn)
        core.start_new_term(self.conn)
        self.assertEqual(core.stats(self.conn)["remaining"], 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
