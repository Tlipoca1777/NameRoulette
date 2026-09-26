"""课表联动抽取的测试。

核心是要能注入「现在几点」，否则周三第几节这类逻辑没法验证。
固定用 2026-09-16（周三，isoweekday=3）作为测试日期。

默认大课区间：
  ① 08:00–09:40（含内部 10 分钟课间 08:45–08:55）
  ② 10:00–11:40
  ③ 14:00–15:40
  ④ 16:00–17:40

运行：python -m unittest tests.test_schedule -v
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core  # noqa: E402
import db  # noqa: E402

WED = 3
THU = 4
SAT = 6


def conn_new():
    return db.connect(":memory:")


def at(hour, minute=0, day=16):
    """2026-09-16 是周三。"""
    return datetime(2026, 9, day, hour, minute)


def seed(conn, cls="一班", n=10):
    for i in range(1, n + 1):
        core.add_student(conn, f"{cls}学生{i:02d}", f"2024{i:04d}", cls)
    return conn


class TestBlocks(unittest.TestCase):
    def setUp(self):
        self.conn = conn_new()

    def test_default_six_blocks(self):
        """默认 6 个大课：上午两节、下午两节、晚上两节（教务课表含晚间）。"""
        blocks = core.list_blocks(self.conn)
        self.assertEqual(len(blocks), 6)
        self.assertEqual([b["idx"] for b in blocks], [1, 2, 3, 4, 5, 6])
        self.assertEqual(blocks[0]["start"], "08:00")
        self.assertEqual(blocks[0]["end"], "09:40")
        self.assertEqual(blocks[2]["start"], "14:00")
        self.assertEqual(blocks[4]["start"], "19:00", "晚上第一节")
        self.assertEqual(blocks[5]["start"], "20:50", "晚上第二节")

    def test_ensure_blocks_adds_missing(self):
        """老库里只有 4 个大课时，导入含晚间的课表要能自动补上 5、6。"""
        c = conn_new()
        c.execute("DELETE FROM blocks WHERE idx > 4")
        c.commit()
        self.assertEqual(len(core.list_blocks(c)), 4)
        db.ensure_blocks(c, 6)
        self.assertEqual(len(core.list_blocks(c)), 6)
        self.assertEqual(core.list_blocks(c)[4]["start"], "19:00")

    def test_default_min_talk_minutes(self):
        self.assertEqual(core.get_min_talk_minutes(self.conn), 30)

    def test_set_min_talk_minutes(self):
        core.set_min_talk_minutes(self.conn, 45)
        self.assertEqual(core.get_min_talk_minutes(self.conn), 45)

    def test_edit_block_time(self):
        core.set_block(self.conn, 1, start="08:10", end="09:50")
        b = core.list_blocks(self.conn)[0]
        self.assertEqual((b["start"], b["end"]), ("08:10", "09:50"))

    def test_blocks_sorted_by_time_not_idx(self):
        """用户把时间改乱后，判定必须按时间顺序，不能按 idx。"""
        core.set_block(self.conn, 1, start="19:00", end="20:40")
        blocks = core.list_blocks(self.conn)
        self.assertEqual(blocks[0]["start"], "10:00", "应最早上课的排在最前")
        self.assertEqual(blocks[-1]["start"], "20:50", "最晚的是晚上第二节")

    def test_current_block(self):
        self.assertEqual(core.current_block(self.conn, at(8, 30))["idx"], 1)
        self.assertIsNone(core.current_block(self.conn, at(9, 50)))
        self.assertEqual(core.current_block(self.conn, at(14, 30))["idx"], 3)


class TestNoScheduleCompat(unittest.TestCase):
    """兼容性底线：没录课表时，行为必须和以前完全一样。"""

    def setUp(self):
        self.conn = conn_new()
        seed(self.conn, "一班", 10)

    def test_not_configured(self):
        self.assertFalse(core.schedule_configured(self.conn))

    def test_pool_is_everyone_regardless_of_time(self):
        for hour in (6, 8, 10, 12, 14, 23):
            st = core.pool_status(self.conn, now=at(hour, 30))
            self.assertEqual(st["counts"]["available"], 10,
                             f"{hour} 点未录课表时不应排除任何人")
            self.assertFalse(st["schedule_active"])

    def test_draw_works_regardless_of_time(self):
        self.assertIsNotNone(core.draw(self.conn, now=at(8, 30)))
        self.assertIsNotNone(core.draw(self.conn, now=at(14, 30)))

    def test_unchecking_all_boxes_means_no_filter(self):
        """把课表全部取消勾选，等于没录课表。"""
        core.set_schedule(self.conn, "一班", WED, 1, has_class=True)
        self.assertTrue(core.schedule_configured(self.conn))
        core.set_schedule(self.conn, "一班", WED, 1, has_class=False)
        self.assertFalse(core.schedule_configured(self.conn))


class TestBusyReason(unittest.TestCase):
    def setUp(self):
        self.conn = conn_new()
        seed(self.conn, "一班", 10)
        # 一班周三：上午①、上午② 有课
        core.set_schedule(self.conn, "一班", WED, 1, has_class=True)
        core.set_schedule(self.conn, "一班", WED, 2, has_class=True)

    def counts(self, hour, minute=0, day=16, **kw):
        return core.pool_status(self.conn, now=at(hour, minute, day), **kw)["counts"]

    def test_in_class_during_block(self):
        c = self.counts(8, 30)
        self.assertEqual((c["available"], c["in_class"]), (0, 10))

    def test_break_inside_block_counts_as_in_class(self):
        """关键：大课内部那 10 分钟课间（08:45–08:55）算上课期间。"""
        c = self.counts(8, 50)
        self.assertEqual((c["available"], c["in_class"]), (0, 10),
                         "课间落在 08:00–09:40 区间内，应算在上课")

    def test_inside_second_block(self):
        self.assertEqual(self.counts(10, 30)["in_class"], 10)

    def test_gap_before_next_block_is_not_free(self):
        """09:45 距 10:00 只有 15 分钟，不够，不能算空闲。"""
        c = self.counts(9, 45)
        self.assertEqual(c["available"], 0)
        self.assertEqual(c["soon"], 10)
        self.assertEqual(c["in_class"], 0)

    def test_lunch_break_is_free(self):
        """12:00 距下午③还有 2 小时，算空闲。"""
        c = self.counts(12, 0)
        self.assertEqual(c["available"], 10)
        self.assertEqual(c["soon"], 0)

    def test_after_school_is_free(self):
        self.assertEqual(self.counts(18, 0)["available"], 10)

    def test_weekend_is_free(self):
        """课表只录了周三，周六就没课。"""
        self.assertEqual(self.counts(8, 30, day=19)["available"], 10)
        self.assertEqual(core.pool_status(self.conn, now=at(8, 30, 19))["weekday"], SAT)

    def test_other_weekday_is_free(self):
        self.assertEqual(self.counts(8, 30, day=17)["available"], 10,
                         "课表只录了周三，周四应空闲")

    def test_block_start_boundary(self):
        """整点开始算上课（start <= now < end）。"""
        self.assertEqual(self.counts(8, 0)["in_class"], 10)
        self.assertEqual(self.counts(9, 39)["in_class"], 10)

    def test_block_end_boundary(self):
        """09:40 已下课；下个自己的大课 10:00，只剩 20 分钟 → 即将上课。"""
        c = self.counts(9, 40)
        self.assertEqual(c["in_class"], 0)
        self.assertEqual(c["soon"], 10)

    def test_min_talk_minutes_affects_result(self):
        """阈值测试：该生只有 10:00 的大课，09:20 时距上课 40 分钟。

        30 分钟算够用 → 空闲；调到 45/60 分钟 → 即将上课。
        （注意不能用 09:20 配「上午①②都有课」的场景，那时还在大课①里面。）
        """
        c = conn_new()
        seed(c, "一班", 10)
        core.set_schedule(c, "一班", WED, 2, has_class=True)   # 只有上午②

        a = core.pool_status(c, now=at(9, 20), min_minutes=30)["counts"]
        self.assertEqual(a["available"], 10, "40 分钟 ≥ 30 分钟，应算空闲")
        self.assertEqual(a["soon"], 0)

        b = core.pool_status(c, now=at(9, 20), min_minutes=45)["counts"]
        self.assertEqual(b["available"], 0, "40 分钟 < 45 分钟，应算即将上课")
        self.assertEqual(b["soon"], 10)

        d = core.pool_status(c, now=at(9, 20), min_minutes=60)["counts"]
        self.assertEqual(d["soon"], 10)

    def test_break_inside_block_with_only_first_block(self):
        """只录上午①有课：08:30 在课内，09:45 已下课且今天没别的课 → 空闲。"""
        c = conn_new()
        seed(c, "一班", 10)
        core.set_schedule(c, "一班", WED, 1, has_class=True)
        self.assertEqual(core.pool_status(c, now=at(8, 30))["counts"]["in_class"], 10)
        self.assertEqual(core.pool_status(c, now=at(9, 45))["counts"]["available"], 10)

    def test_soon_detail_reports_minutes(self):
        st = core.pool_status(self.conn, now=at(9, 45))
        self.assertIsNotNone(st["soon_detail"])
        self.assertEqual(st["soon_detail"]["minutes"], 15)
        self.assertIn("大课", st["soon_detail"]["block"])

    def test_untalked_is_time_independent(self):
        self.assertEqual(self.counts(8, 30)["available"], 0)
        self.assertEqual(core.pool_status(self.conn, now=at(8, 30))["untalked"], 10)


class TestExceptions(unittest.TestCase):
    def setUp(self):
        self.conn = conn_new()
        seed(self.conn, "一班", 4)
        core.set_schedule(self.conn, "一班", WED, 1, has_class=True)
        self.ids = [s["id"] for s in core.list_students(self.conn)]

    def test_extra_class_excludes_student(self):
        """全班周三①没课，某人额外有选修课 → 他被排除，其他人在池子里。"""
        core.set_schedule(self.conn, "一班", WED, 1, has_class=False)
        core.clear_students(self.conn)  # 清掉再重建，保证干净
        seed(self.conn, "一班", 4)
        ids = [s["id"] for s in core.list_students(self.conn)]
        core.set_schedule(self.conn, "一班", WED, 2, has_class=True)

        extra = ids[0]
        core.set_exception(self.conn, extra, WED, 1, has_class=True)
        st = core.pool_status(self.conn, now=at(8, 30))
        names = [s["name"] for s in st["available"]]
        self.assertNotIn(core.list_students(self.conn)[0]["name"], names)
        self.assertEqual(st["counts"]["in_class"], 1)
        self.assertEqual(st["counts"]["available"], 3)

    def test_skip_class_includes_student(self):
        """全班周三①有课，但某人这节不上 → 他算空闲。"""
        core.set_exception(self.conn, self.ids[0], WED, 1, has_class=False)
        st = core.pool_status(self.conn, now=at(8, 30))
        self.assertEqual(st["counts"]["in_class"], 3)
        self.assertEqual(st["counts"]["available"], 1)
        self.assertEqual(st["available"][0]["id"], self.ids[0])

    def test_clear_exception(self):
        core.set_exception(self.conn, self.ids[0], WED, 1, has_class=False)
        self.assertEqual(core.pool_status(self.conn, now=at(8, 30))["counts"]["available"], 1)
        core.clear_exception(self.conn, self.ids[0], WED, 1)
        self.assertEqual(core.pool_status(self.conn, now=at(8, 30))["counts"]["available"], 0,
                         "取消例外后应恢复跟随班级课表")

    def test_exception_alone_marks_configured(self):
        c = conn_new()
        seed(c, "一班", 2)
        core.set_schedule(c, "一班", WED, 1, has_class=False)   # 取消勾选，仅此
        self.assertFalse(core.schedule_configured(c))
        core.set_exception(c, core.list_students(c)[0]["id"], WED, 1, has_class=True)
        self.assertTrue(core.schedule_configured(c))

    def test_list_exceptions_carries_name(self):
        core.set_exception(self.conn, self.ids[0], WED, 1, has_class=True, note="选修")
        rows = core.list_exceptions(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["note"], "选修")
        self.assertTrue(rows[0]["name"])


class TestPoolAndDraw(unittest.TestCase):
    def setUp(self):
        self.conn = conn_new()
        # 两个班，课表不同
        seed(self.conn, "一班", 10)
        seed(self.conn, "二班", 10)
        core.set_schedule(self.conn, "一班", WED, 1, has_class=True)   # 一班周三①有课
        core.set_schedule(self.conn, "二班", WED, 3, has_class=True)   # 二班周三③有课

    def test_pool_only_has_free_students(self):
        st = core.pool_status(self.conn, now=at(8, 30))
        self.assertEqual(st["counts"]["available"], 10)
        self.assertTrue(all(s["cls"] == "二班" for s in st["available"]),
                        "8:30 只有二班没课")

    def test_draw_never_picks_busy_student(self):
        for _ in range(10):
            r = core.draw(self.conn, now=at(8, 30))
            self.assertIsNotNone(r)
            self.assertEqual(r["cls"], "二班", f"8:30 抽到了有课的一班：{r}")
        self.assertIsNone(core.draw(self.conn, now=at(8, 30)), "二班抽完了就该空了")

    def test_draw_returns_none_when_all_busy(self):
        core.set_schedule(self.conn, "二班", WED, 1, has_class=True)   # 二班周三①也有课
        self.assertIsNone(core.draw(self.conn, now=at(8, 30)))
        st = core.pool_status(self.conn, now=at(8, 30))
        self.assertEqual(st["counts"]["available"], 0)
        self.assertEqual(st["counts"]["in_class"], 20)

    def test_ignore_schedule_forces_draw(self):
        core.set_schedule(self.conn, "二班", WED, 1, has_class=True)
        self.assertEqual(
            core.pool_status(self.conn, now=at(8, 30))["counts"]["available"], 0)
        st = core.pool_status(self.conn, now=at(8, 30), ignore_schedule=True)
        self.assertEqual(st["counts"]["available"], 20)
        self.assertFalse(st["schedule_active"])
        self.assertIsNotNone(core.draw(self.conn, now=at(8, 30), ignore_schedule=True))

    def test_drawn_students_are_excluded(self):
        core.draw(self.conn, now=at(8, 30))
        st = core.pool_status(self.conn, now=at(8, 30))
        self.assertEqual(st["counts"]["available"], 9)
        self.assertEqual(st["counts"]["drawn"], 1)
        self.assertEqual(st["untalked"], 19, "未谈人数不受时间影响：20 人减掉已抽的 1 人")

    def test_disabled_counted_separately(self):
        core.set_enabled(self.conn, core.list_students(self.conn)[0]["id"], False)
        st = core.pool_status(self.conn, now=at(8, 30))
        self.assertEqual(st["counts"]["disabled"], 1)

    def test_next_free_hint(self):
        hint = core.next_free_hint(self.conn, now=at(9, 45))
        self.assertIsNotNone(hint)
        self.assertEqual(hint["at"], "11:40", "大课间里应提示下一个下课时刻")


class TestStartNewTerm(unittest.TestCase):
    def setUp(self):
        self.conn = conn_new()
        seed(self.conn, "一班", 5)

    def test_restores_all_and_keeps_history(self):
        for _ in range(3):
            core.draw(self.conn)
        res = core.start_new_term(self.conn)
        self.assertEqual(res["restored"], 3)
        self.assertEqual(res["new_round"], 2)
        self.assertEqual(res["new_term"], 2)
        self.assertEqual(core.pool_status(self.conn)["counts"]["available"], 5)
        self.assertEqual(len(core.history(self.conn)), 3, "历史必须保留")

    def test_does_not_revive_disabled(self):
        seed(self.conn, "二班", 3)
        target = core.list_students(self.conn)[0]
        core.set_enabled(self.conn, target["id"], False)
        core.draw(self.conn)
        core.start_new_term(self.conn)
        self.assertNotIn(target["name"], [s["name"] for s in core.pool_status(self.conn)["available"]])

    def test_term_is_reflected_in_history(self):
        core.draw(self.conn)
        core.start_new_term(self.conn)
        core.draw(self.conn)
        self.assertEqual(len(core.history(self.conn, round=1)), 1)
        self.assertEqual(len(core.history(self.conn, round=2)), 1)


class TestScheduleGrid(unittest.TestCase):
    def setUp(self):
        self.conn = conn_new()
        seed(self.conn, "一班", 3)
        seed(self.conn, "二班", 3)

    def test_grid_lists_classes_from_roster(self):
        g = core.schedule_grid(self.conn)
        self.assertIn("一班", g["classes"])
        self.assertIn("二班", g["classes"])
        self.assertEqual(len(g["blocks"]), 6)
        self.assertEqual(g["cells"], {})
        self.assertFalse(g["configured"])

    def test_set_and_read_cell(self):
        core.set_schedule(self.conn, "一班", WED, 2, has_class=True, note="高等数学")
        g = core.schedule_grid(self.conn)
        cell = g["cells"][f"一班|{WED}|2"]
        self.assertEqual(cell["has_class"], 1)
        self.assertEqual(cell["note"], "高等数学")
        self.assertTrue(g["configured"])

    def test_uncheck_without_note_removes_row(self):
        core.set_schedule(self.conn, "一班", WED, 2, has_class=True)
        core.set_schedule(self.conn, "一班", WED, 2, has_class=False)
        g = core.schedule_grid(self.conn)
        self.assertEqual(g["cells"], {}, "取消勾选且无备注就应删除该行")

    def test_uncheck_with_note_keeps_row(self):
        core.set_schedule(self.conn, "一班", WED, 2, has_class=False, note="调课")
        g = core.schedule_grid(self.conn)
        self.assertEqual(g["cells"][f"一班|{WED}|2"]["has_class"], 0)

    def test_empty_cls_rejected(self):
        with self.assertRaises(ValueError):
            core.set_schedule(self.conn, "  ", WED, 1)


class TestBackupWithSchedule(unittest.TestCase):
    def test_roundtrip_includes_schedule(self):
        src = conn_new()
        seed(src, "一班", 5)
        core.set_schedule(src, "一班", WED, 1, has_class=True, note="高数")
        core.set_schedule(src, "一班", THU, 3, has_class=True)
        core.set_exception(src, core.list_students(src)[0]["id"], WED, 4,
                           has_class=True, note="选修")
        core.set_min_talk_minutes(src, 45)
        backup = core.export_backup_text(src)

        dst = conn_new()
        core.import_backup_text(dst, backup)

        self.assertTrue(core.schedule_configured(dst))
        self.assertEqual(core.get_min_talk_minutes(dst), 45)
        g = core.schedule_grid(dst)
        self.assertEqual(g["cells"][f"一班|{WED}|1"]["note"], "高数")
        self.assertEqual(g["cells"][f"一班|{THU}|3"]["has_class"], 1)
        self.assertEqual(len(core.list_exceptions(dst)), 1)

        # 判定结果也应一致
        a = core.pool_status(src, now=at(8, 30))["counts"]
        b = core.pool_status(dst, now=at(8, 30))["counts"]
        self.assertEqual(a, b)

    def test_blocks_restored(self):
        src = conn_new()
        seed(src, "一班", 2)
        core.set_block(src, 1, start="08:10", end="09:50")
        dst = conn_new()
        core.import_backup_text(dst, core.export_backup_text(src))
        self.assertEqual(core.list_blocks(dst)[0]["start"], "08:10")


class TestScheduleCsv(unittest.TestCase):
    def setUp(self):
        self.conn = conn_new()
        seed(self.conn, "一班", 3)
        seed(self.conn, "二班", 3)

    def test_import_basic(self):
        text = "班级,星期,大课,课程\n一班,3,1,高等数学\n一班,3,2,大学英语\n二班,4,3,线性代数\n"
        res = core.import_schedule_csv(self.conn, text)
        self.assertEqual(res["added"], 3, res)
        self.assertEqual(res["errors"], [])
        self.assertTrue(core.schedule_configured(self.conn))
        g = core.schedule_grid(self.conn)
        self.assertEqual(g["cells"]["一班|3|1"]["note"], "高等数学")

    def test_import_accepts_chinese_weekday_and_block_name(self):
        text = "班级,星期,大课,课程\n一班,周三,上午第一节大课,高数\n"
        res = core.import_schedule_csv(self.conn, text)
        self.assertEqual(res["added"], 1, res)
        g = core.schedule_grid(self.conn)
        self.assertEqual(g["cells"]["一班|3|1"]["has_class"], 1)

    def test_import_accepts_circled_short_block(self):
        text = "班级,星期,大课,课程\n一班,周三,上①,高数\n一班,周三,下②,体育\n"
        core.import_schedule_csv(self.conn, text)
        g = core.schedule_grid(self.conn)
        self.assertIn("一班|3|1", g["cells"])
        self.assertIn("一班|3|4", g["cells"])

    def test_import_is_merge_not_replace(self):
        core.set_schedule(self.conn, "一班", 1, 1, has_class=True, note="旧")
        core.import_schedule_csv(self.conn, "班级,星期,大课,课程\n二班,2,1,新\n")
        g = core.schedule_grid(self.conn)
        self.assertIn("一班|1|1", g["cells"], "导入不应清掉已有课表")
        self.assertIn("二班|2|1", g["cells"])

    def test_bad_rows_reported_not_crash(self):
        text = ("班级,星期,大课,课程\n"
                "一班,周三,1,正常\n"
                "一班,周八,1,星期不对\n"
                "一班,周三,99,大课不对\n"
                "三班,周三,1,新班级\n")
        res = core.import_schedule_csv(self.conn, text)
        self.assertGreaterEqual(len(res["errors"]), 2, res["errors"])
        self.assertIn("三班|3|1", core.schedule_grid(self.conn)["cells"],
                      "名单里没有的班级也应允许（方便先录课表）")

    def test_import_exception_row_with_at_name(self):
        text = "班级,星期,大课,课程\n@一班学生01,3,1,额外有课｜选修·心理学\n"
        res = core.import_schedule_csv(self.conn, text)
        self.assertEqual(res["exceptions"], 1, res)
        rows = core.list_exceptions(self.conn)
        self.assertEqual(rows[0]["note"], "选修·心理学")
        self.assertEqual(rows[0]["has_class"], 1)
        self.assertTrue(core.schedule_configured(self.conn))

    def test_import_exception_skip_class(self):
        text = "班级,星期,大课,课程\n@一班学生02,3,2,这节不上｜\n"
        core.import_schedule_csv(self.conn, text)
        self.assertEqual(core.list_exceptions(self.conn)[0]["has_class"], 0)

    def test_export_then_import_roundtrip(self):
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True,
                          note="高数", weeks="1-8,10-16")
        core.set_schedule(self.conn, "二班", 5, 4, has_class=True)
        text = core.export_schedule_csv(self.conn)
        self.assertTrue(text.startswith("\ufeff"), "导出应带 BOM")
        self.assertIn("班级,星期,大课,周次,课程", text)
        self.assertIn("1-8,10-16", text)

        fresh = conn_new()
        core.import_schedule_csv(fresh, text)
        self.assertEqual(core.schedule_grid(self.conn)["cells"],
                         core.schedule_grid(fresh)["cells"],
                         "课表部分往返应一致")
        self.assertEqual(
            core.schedule_grid(fresh)["cells"]["一班|3|1"]["weeks"], "1-8,10-16")

    def test_old_four_column_format_still_works(self):
        """旧格式（班级,星期,大课,课程）没有周次列，应仍然可用，周次视为每周。"""
        res = core.import_schedule_csv(self.conn, "班级,星期,大课,课程\n一班,3,1,高数\n")
        self.assertEqual(res["added"], 1, res)
        cell = core.schedule_grid(self.conn)["cells"]["一班|3|1"]
        self.assertEqual(cell["note"], "高数")
        self.assertEqual(cell["weeks"], "", "旧格式的课应视为每周都有")

    def test_clear_schedule(self):
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True)
        core.set_exception(self.conn, core.list_students(self.conn)[0]["id"], 3, 1)
        res = core.clear_schedule(self.conn)
        self.assertGreaterEqual(res["deleted"], 1)
        self.assertFalse(core.schedule_configured(self.conn))
        self.assertEqual(core.list_exceptions(self.conn), [])

    def test_empty_import(self):
        res = core.import_schedule_csv(self.conn, "")
        self.assertEqual(res["added"], 0)
        self.assertEqual(res["errors"], [])


class TestScheduleExcel(unittest.TestCase):
    """课表支持 .xlsx 导入。关键点：多工作表时要按「课时数」挑表，
    不能沿用导入名单那套「按学生数挑」，否则会挑到名单/封面页。"""

    def setUp(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("未安装 openpyxl")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.conn = conn_new()
        seed(self.conn, "一班", 3)
        seed(self.conn, "二班", 3)

    def tearDown(self):
        self.tmp.cleanup()

    def make_xlsx(self, sheets, name="课表.xlsx"):
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
        path = self.make_xlsx({"课表": [
            ["班级", "星期", "大课", "课程"],
            ["一班", 3, 1, "高等数学"],
            ["一班", 3, 2, "大学英语"],
            ["二班", 4, 3, "线性代数"],
        ]})
        res = core.import_schedule_from_file(self.conn, path)
        self.assertEqual(res["added"], 3, res)
        self.assertEqual(res["errors"], [])
        cells = core.schedule_grid(self.conn)["cells"]
        self.assertEqual(cells["一班|3|1"]["note"], "高等数学")
        self.assertIn("课表", res["source"])

    def test_title_row_above_header(self):
        """首行是「某某班课表」这类标题时也要能导入。"""
        path = self.make_xlsx({"Sheet1": [
            ["计算机2401班课程表", None, None, None],
            ["班级", "星期", "大课", "课程"],
            ["一班", 3, 1, "高等数学"],
        ]})
        res = core.import_schedule_from_file(self.conn, path)
        self.assertEqual(res["added"], 1, res)
        self.assertIn("一班|3|1", core.schedule_grid(self.conn)["cells"])

    def test_picks_schedule_sheet_not_roster_sheet(self):
        """第一张是名单表时，不能挑到名单表去。"""
        path = self.make_xlsx({
            "学生名单": [["姓名", "学号", "班级"]] + [
                [f"学生{i}", f"2024{i:04d}", "一班"] for i in range(1, 21)
            ],
            "课程表": [
                ["班级", "星期", "大课", "课程"],
                ["一班", 3, 1, "高等数学"],
                ["二班", 5, 4, "体育"],
            ],
        })
        res = core.import_schedule_from_file(self.conn, path)
        self.assertEqual(res["added"], 2, f"应挑到课程表而不是学生名单：{res}")
        self.assertIn("课程表", res["source"],
                      f"挑错了工作表：{res['source']}")

    def test_chinese_weekday_and_block_name_in_xlsx(self):
        path = self.make_xlsx({"课表": [
            ["班级", "星期", "大课", "课程"],
            ["一班", "周三", "上午第一节大课", "高数"],
            ["一班", "周五", "下②", "体育"],
        ]})
        core.import_schedule_from_file(self.conn, path)
        cells = core.schedule_grid(self.conn)["cells"]
        self.assertIn("一班|3|1", cells)
        self.assertIn("一班|5|4", cells)

    def test_exception_row_in_xlsx(self):
        path = self.make_xlsx({"课表": [
            ["班级", "星期", "大课", "课程"],
            ["@一班学生01", 3, 1, "额外有课｜选修"],
        ]})
        res = core.import_schedule_from_file(self.conn, path)
        self.assertEqual(res["exceptions"], 1, res)
        self.assertEqual(core.list_exceptions(self.conn)[0]["note"], "选修")

    def test_csv_still_works_via_same_entry(self):
        path = os.path.join(self.dir, "课表.csv")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("班级,星期,大课,课程\n一班,3,1,高数\n")
        res = core.import_schedule_from_file(self.conn, path)
        self.assertEqual(res["added"], 1, res)

    def test_corrupt_xls_gives_clear_error(self):
        path = os.path.join(self.dir, "损坏.xls")
        with open(path, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0")
        with self.assertRaises(ValueError) as ctx:
            core.import_schedule_from_file(self.conn, path)
        self.assertIn("读不了", str(ctx.exception))

    def test_xlsx_import_is_merge(self):
        core.set_schedule(self.conn, "一班", 1, 1, has_class=True, note="原有")
        path = self.make_xlsx({"课表": [
            ["班级", "星期", "大课", "课程"],
            ["二班", 2, 1, "新增"],
        ]})
        core.import_schedule_from_file(self.conn, path)
        cells = core.schedule_grid(self.conn)["cells"]
        self.assertIn("一班|1|1", cells, "xlsx 导入也不应清掉已有课表")
        self.assertIn("二班|2|1", cells)

    def test_dry_run_does_not_write(self):
        text = "班级,星期,大课,课程\n一班,3,1,高数\n"
        res = core.import_schedule_csv(self.conn, text, dry_run=True)
        self.assertEqual(res["parsed"], 1)
        self.assertEqual(core.schedule_grid(self.conn)["cells"], {},
                         "干跑不应写入任何数据")


class TestWeeks(unittest.TestCase):
    """教学周支持：时段有时有课、有时没课（每周课表不一样的学校）。"""

    def setUp(self):
        self.conn = conn_new()
        seed(self.conn, "一班", 6)

    # ---------------- 周次写法解析
    def test_normalize_weeks(self):
        cases = [
            ("", ""),
            ("1-16", "1-16"),
            ("1-8,10-16", "1-8,10-16"),
            ("1,3,5", "1,3,5"),
            ("1,2,3,5", "1-3,5"),
            ("第1-8周", "1-8"),
            ("1-4周,6周", "1-4,6"),
            ("单周", "单"),
            ("双", "双"),
            ("8-1", "1-8"),
            (" 1 - 3 , 5 ", "1-3,5"),
        ]
        for raw, want in cases:
            self.assertEqual(core.normalize_weeks(raw), want, f"输入 {raw!r}")

    def test_weeks_match(self):
        self.assertTrue(core.weeks_match("", 5), "空 = 每周都有")
        self.assertTrue(core.weeks_match("1-16", 5))
        self.assertFalse(core.weeks_match("1-8", 9))
        self.assertTrue(core.weeks_match("1-8,10-16", 12))
        self.assertFalse(core.weeks_match("1-8,10-16", 9))
        self.assertTrue(core.weeks_match("单", 3))
        self.assertFalse(core.weeks_match("单", 4))
        self.assertTrue(core.weeks_match("双", 4))
        self.assertFalse(core.weeks_match("双", 5))
        # 算不出周次时按有课处理，宁可少抽也不要抽到在上课的人
        self.assertTrue(core.weeks_match("1-8", None))

    # ---------------- 当前周次计算
    def test_current_week(self):
        core.set_term_start(self.conn, "2026-09-07")     # 周一
        self.assertEqual(core.current_week(self.conn, datetime(2026, 9, 7, 10)), 1)
        self.assertEqual(core.current_week(self.conn, datetime(2026, 9, 13, 10)), 1)
        self.assertEqual(core.current_week(self.conn, datetime(2026, 9, 14, 10)), 2)
        self.assertEqual(core.current_week(self.conn, datetime(2026, 10, 5, 10)), 5)

    def test_current_week_without_term_start(self):
        self.assertIsNone(core.current_week(self.conn, datetime(2026, 9, 7)))

    def test_current_week_before_term(self):
        core.set_term_start(self.conn, "2026-09-07")
        self.assertIsNone(core.current_week(self.conn, datetime(2026, 8, 1)))

    def test_set_term_start_validates(self):
        with self.assertRaises(ValueError):
            core.set_term_start(self.conn, "2026/09/07")
        self.assertEqual(core.get_term_start(self.conn), "")
        core.set_term_start(self.conn, "2026-09-07")
        self.assertEqual(core.get_term_start(self.conn), "2026-09-07")

    # ---------------- 按周判断出勤（核心）
    def test_slot_free_after_its_weeks_end(self):
        """周三上午① 只有 1-8 周有课 —— 第 9 周就该是空闲的。"""
        core.set_term_start(self.conn, "2026-09-07")
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True,
                          note="高数", weeks="1-8")

        # 第 3 周（2026-09-21 那一周）周三
        week3 = datetime(2026, 9, 23, 8, 30)
        st = core.pool_status(self.conn, now=week3)
        self.assertEqual(st["week"], 3, "应算出第 3 周")
        self.assertEqual(st["counts"]["in_class"], 6, "第 3 周有课")

        # 第 9 周（2026-11-02 那一周）周三 —— 同一时段已经没课了
        week9 = datetime(2026, 11, 4, 8, 30)
        st9 = core.pool_status(self.conn, now=week9)
        self.assertEqual(st9["week"], 9)
        self.assertEqual(st9["counts"]["in_class"], 0, "第 9 周该时段已结束，应空闲")
        self.assertEqual(st9["counts"]["available"], 6)

    def test_odd_even_weeks(self):
        core.set_term_start(self.conn, "2026-09-07")
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True, weeks="单")
        odd = datetime(2026, 9, 23, 8, 30)      # 第 3 周（单）
        even = datetime(2026, 9, 30, 8, 30)     # 第 4 周（双）
        self.assertEqual(core.pool_status(self.conn, now=odd)["counts"]["in_class"], 6)
        self.assertEqual(core.pool_status(self.conn, now=even)["counts"]["in_class"], 0)

    def test_draw_only_picks_free_by_week(self):
        core.set_term_start(self.conn, "2026-09-07")
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True,
                          note="高数", weeks="1-8")
        inWeeks = core.pool_status(self.conn, now=datetime(2026, 9, 23, 8, 30))
        self.assertIsNone(core.draw(self.conn, now=datetime(2026, 9, 23, 8, 30)))
        self.assertEqual(inWeeks["counts"]["available"], 0)

        core2 = conn_new()
        seed(core2, "一班", 6)
        core2.execute("UPDATE students SET drawn=0")
        core.set_term_start(core2, "2026-09-07")
        core.set_schedule(core2, "一班", 3, 1, has_class=True, weeks="1-8")
        self.assertIsNotNone(core.draw(core2, now=datetime(2026, 11, 4, 8, 30)),
                             "第 9 周该时段没课，应该能抽")

    def test_weeks_written_via_set_schedule(self):
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True, weeks="1-4周,6周")
        self.assertEqual(core.schedule_grid(self.conn)["cells"]["一班|3|1"]["weeks"],
                         "1-4,6")

    # ---------------- 没设学期开始日期时的保守行为
    def test_unresolved_warning_when_weeks_used_without_term_start(self):
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True, weeks="1-8")
        st = core.pool_status(self.conn)
        self.assertIsNone(st["week"])
        self.assertTrue(st["week_unresolved"], "应提醒用户没设学期开始日期")
        # 算不出周次 → 按有课处理（保守）
        self.assertEqual(core.pool_status(
            self.conn, now=datetime(2026, 11, 4, 8, 30))["counts"]["in_class"], 6)

    def test_no_warning_when_weeks_not_used(self):
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True)   # 每周
        st = core.pool_status(self.conn)
        self.assertFalse(st["week_unresolved"])

    def test_exceptions_are_week_independent(self):
        """个别学生例外暂不区分周次：整学期都生效。"""
        sid = core.list_students(self.conn)[0]["id"]
        core.set_exception(self.conn, sid, 3, 1, has_class=False)
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True, weeks="1-4")
        for day, week in ((datetime(2026, 9, 23, 8, 30), 3), (datetime(2026, 11, 4, 8, 30), 9)):
            st = core.pool_status(self.conn, now=day)
            names = [s["id"] for s in st["available"]]
            self.assertIn(sid, names, f"第 {week} 周该生仍应因例外而空闲")

    def test_backup_keeps_weeks_and_term_start(self):
        """备份必须带上 weeks 和 term_start。

        曾漏掉这两项：走「导出备份 → 导入备份」转移数据后，
        周次全变成"每周有课"、学期开始日期丢失，判定会静默出错。
        """
        core.set_term_start(self.conn, "2026-08-31")
        core.set_schedule(self.conn, "一班", 3, 1, has_class=True,
                          weeks="1-8", note="数据结构")
        dst = conn_new()
        core.import_backup_text(dst, core.export_backup_text(self.conn))

        self.assertEqual(core.get_term_start(dst), "2026-08-31")
        cell = core.schedule_grid(dst)["cells"]["一班|3|1"]
        self.assertEqual(cell["weeks"], "1-8", cell)
        self.assertEqual(cell["note"], "数据结构", cell)

        # 挑一个能区分对错的时刻：第 10 周该时段已经没课了
        t = datetime(2026, 11, 4, 8, 30)
        self.assertEqual(core.pool_status(self.conn, now=t)["counts"],
                         core.pool_status(dst, now=t)["counts"])




class TestJiaowuFormat(unittest.TestCase):
    """教务系统导出的二维课表（学生个人课表 .xls）。

    用真实文件测：文件名带学号、格子是「课程/教师/周次/教室」四行、
    一个格子里可能有多门课（周次各不相同）、含晚上两个时段。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def setUp(self):
        try:
            import xlrd  # noqa: F401
        except ImportError:
            self.skipTest("未安装 xlrd，无法读 .xls")
        # 自动找目录下的课表样本（换新表后放回来即可生效，不用改测试）
        import glob
        found = sorted(glob.glob(os.path.join(self.ROOT, "*课表*.xls")) +
                       glob.glob(os.path.join(self.ROOT, "samples", "*.xls")))
        if not found:
            self.skipTest("目录里没有课表样本（学生个人课表_*.xls），跳过真实文件测试")
        self.FILE = found[0]
        import jiaowu
        self.jiaowu = jiaowu
        self.conn = conn_new()

    # ---------------- 解析
    def test_parse_meta(self):
        g = self.jiaowu.parse_file(self.FILE)
        self.assertEqual(g["meta"].get("班级"), "242013")
        self.assertEqual(g["max_slot"], 6, "文件含晚上两个时段")
        self.assertTrue(20 <= len(g["cells"]) <= 30, f"时段数={len(g['cells'])}")

    def test_parse_week_union_of_multiple_courses(self):
        """周五第五六节：运筹学 1-8 + 电子工艺 11 → 并集 1-8,11。"""
        g = self.jiaowu.parse_file(self.FILE)
        cell = g["cells"][(5, 3)]
        self.assertEqual(cell["weeks"], "1-8,11", cell)
        self.assertIn("运筹学", cell["note"])
        self.assertIn("电子工艺技术训练B", cell["note"])

    def test_parse_course_block_fields(self):
        """格子里每门课是 4 行：课程名/教师/周次/教室，别串位。"""
        g = self.jiaowu.parse_file(self.FILE)
        courses = g["cells"][(5, 4)]["courses"]      # 周五 第七八节
        target = next(c for c in courses if c["name"] == "软件质量保证与测试")
        self.assertEqual(target["teacher"], "万露")
        self.assertEqual(target["room"], "D308/10")
        self.assertEqual(target["weeks"], "6-9")

    def test_week_11_is_full_day(self):
        """「电子工艺技术训练B」第 11 周占满全天。"""
        g = self.jiaowu.parse_file(self.FILE)
        for wd in (1, 2, 3, 4, 5):
            cell = g["cells"].get((wd, 1))
            self.assertIsNotNone(cell, f"周{wd}第一大课应有课")
            self.assertIn(11, core.parse_week_set(cell["weeks"]),
                          f"周{wd}第 11 周应有课，实际 {cell['weeks']}")

    # ---------------- 导入到班级
    def test_default_is_class_target(self):
        """默认按班级导入：教务文件叫「学生个人课表」，但辅导员通常要给全班用。
        曾经默认按学号写成个人课表，结果只排除一个人，同班其他同学照旧能抽到。"""
        seed(self.conn, "242013", 4)
        res = core.import_schedule_from_file(self.conn, self.FILE)
        self.assertIn("班级 242013", res.get("target", ""), res)
        self.assertEqual(core.students_with_own_schedule(self.conn), [])
        self.assertTrue(core.schedule_configured(self.conn))

    def test_unknown_class_is_rejected(self):
        """课表里的班级在名单里不存在时必须报错，否则导入后完全不生效还看不出来。"""
        seed(self.conn, "软件工程2401", 4)
        res = core.import_schedule_from_file(self.conn, self.FILE)
        self.assertIn("error", res, res)
        self.assertIn("不存在", res["error"])
        self.assertFalse(core.schedule_configured(self.conn))

    def test_import_as_class(self):
        seed(self.conn, "242013", 4)
        res = core.import_schedule_from_file(self.conn, self.FILE, target="class")
        self.assertGreaterEqual(res["added"], 20, res)
        self.assertIn("班级 242013", res["target"])
        cells = core.schedule_grid(self.conn)["cells"]
        self.assertEqual(cells["242013|5|3"]["weeks"], "1-8,11")

    def test_blocks_extended_to_six(self):
        core.import_schedule_from_file(self.conn, self.FILE, target="class")
        self.assertEqual(len(core.list_blocks(self.conn)), 6)

    # ---------------- 导入到学生（个人课表）
    def test_import_as_student_by_filename_sid(self):
        """只适用于「学生个人课表_学号.xls」这类文件名带学号的样本。"""
        import re as _re
        m = _re.search(r"(\d{6,})", os.path.basename(self.FILE))
        if not m:
            self.skipTest("样本文件名里没有学号，跳过（班级课表不适用）")
        sid_num = m.group(1)
        seed(self.conn, "242013", 3)
        sid = core.list_students(self.conn)[0]["id"]
        core.update_student(self.conn, sid, sid=sid_num)
        res = core.import_schedule_from_file(self.conn, self.FILE, target="student")
        self.assertIn("个人课表", res.get("target", ""), res)
        own = core.students_with_own_schedule(self.conn)
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["sid"], sid_num)

    def test_own_schedule_overrides_class(self):
        """有个人课表的学生完全按自己的判断，不看班级课表。"""
        seed(self.conn, "242013", 2)
        ids = [s["id"] for s in core.list_students(self.conn)]
        core.update_student(self.conn, ids[0], sid="24201320")
        core.set_schedule(self.conn, "242013", 1, 1, has_class=True)     # 班级：全程
        core.set_student_schedule(self.conn, ids[0], 1, 1,
                                  has_class=True, weeks="11")            # 个人：仅第 11 周
        core.set_term_start(self.conn, "2026-09-07")

        week3_mon = datetime(2026, 9, 21, 8, 30)      # 第 3 周周一 第一大课
        st = core.pool_status(self.conn, now=week3_mon)
        self.assertEqual(st["week"], 3)
        self.assertEqual(st["counts"]["in_class"], 1, "按班级课表的那位应在上课")
        self.assertEqual([s["id"] for s in st["available"]], [ids[0]],
                         "有个人课表的同学第 3 周该时段应空闲")

    def test_import_target_student_without_match_errors(self):
        res = core.import_schedule_from_file(self.conn, self.FILE, target="student")
        self.assertIn("error", res, res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
