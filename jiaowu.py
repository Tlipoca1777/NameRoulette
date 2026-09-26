"""教务系统导出的「二维课表」解析。

识别这种结构（南昌航空大学那套教务系统的导出格式）：

    行0  南昌航空大学  胡智凌  学生个人课表
    行1  学年学期：2026-2027-1  班级：242013  专业：软件工程  院系：软件学院
    行2  ·  星期一  星期二  星期三  星期四  星期五  星期六  星期日
    行3  第一二节   [各星期的课]
    行4  第三四节   ...
    行5  第五六节   ...
    行6  第七八节   ...
    行7  第九十节   ...
    行8  第十一、十二节
    行9  课程汇总行（忽略）

每个格子里的每门课占 4 行：课程名 / 教师 / 周次([周])[节次] / 教室。
一个格子可以有多门课，各自周次不同，例如「周五第五六节」是三门课首尾相接。

关键处理：一个时段「有课的周次」= 该格所有课程周次的**并集**。
对「此刻有没有课」的判断而言这是正确的——只要有一门课占着，
学生就不在教室外。
"""
from __future__ import annotations

import re
from pathlib import Path

from core import format_weeks, normalize_weeks, parse_week_set

# 星期表头 → 1..7
WEEKDAY_HEADERS = {}
for _n, _i in (("一", 1), ("二", 2), ("三", 3), ("四", 4), ("五", 5), ("六", 6),
               ("日", 7), ("天", 7)):
    for _p in ("星期", "周", "礼拜"):
        WEEKDAY_HEADERS[_p + _n] = _i

# 节次标签 → 第几个大课（两个小节算一个大课）
SLOT_LABELS = {
    "第一二节": 1, "第一、二节": 1, "第1-2节": 1, "第1、2节": 1, "第1,2节": 1,
    "第三四节": 2, "第三、四节": 2, "第3-4节": 2, "第3、4节": 2, "第3,4节": 2,
    "第五六节": 3, "第五、六节": 3, "第5-6节": 3, "第5、6节": 3, "第5,6节": 3,
    "第七八节": 4, "第七、八节": 4, "第7-8节": 4, "第7、8节": 4, "第7,8节": 4,
    "第九十节": 5, "第九、十节": 5, "第9-10节": 5, "第9、10节": 5, "第9,10节": 5,
    "第十一、十二节": 6, "第十一十二节": 6, "第11-12节": 6, "第11、12节": 6,
}

# 周次那一行有两种模板写法：
#   学生个人课表：  11([周])[01-02-...-08节]
#   班级课表：     （11周[01-02节]）        ← 全角括号，周字在后，没有 ([周])
WEEKS_PATTERNS = (
    re.compile(r"(?P<weeks>[^()（）\[\]]*?)\(\[周\]\)\s*\[(?P<periods>[^\]]*)\]"),
    re.compile(r"[（(]\s*(?P<weeks>[^（()）\[\]]*?)\s*周\s*"
               r"\[(?P<periods>[^\]]*)\]\s*[）)]?"),
)


def _match_weeks_line(line: str):
    """能匹配上就返回 match，否则 None。"""
    for pat in WEEKS_PATTERNS:
        m = pat.search(line)
        if m:
            return m
    return None

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_number(text: str) -> int | None:
    """把「九」「十」「十一」「十二」这类中文数词转成数字。"""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):                     # 十一、十二
        return 10 + _CN_DIGITS.get(text[1:], 0)
    if text.startswith("二") and len(text) > 1:   # 二十
        return 20
    return _CN_DIGITS.get(text[0])


def slot_from_label(label) -> int | None:
    """「第一二节」→ 1，「第五六节」→ 3，「第十一、十二节」→ 6。"""
    raw = str(label or "").strip().replace(" ", "")
    if not raw:
        return None
    if raw in SLOT_LABELS:
        return SLOT_LABELS[raw]

    # 兜底：解析出第一个小节号，再按「两小节一个大课」折算
    m = re.search(r"\d+", raw)
    if m:
        return (int(m.group()) + 1) // 2
    # 中文数词写法，例如「第九节」「第十一十二节」
    body = raw.lstrip("第").rstrip("节")
    m = re.match(r"([一二三四五六七八九十两]+)", body)
    if m:
        n = _cn_number(m.group(1))
        if n:
            return (n + 1) // 2
    return None


def parse_cell(text) -> list:
    """解析一个格子里的所有课程。每门课占 4 行，行数不足或格式不符就跳过。"""
    lines = [l.strip() for l in str(text or "").split("\n")]
    lines = [l for l in lines if l]
    out = []
    for i, line in enumerate(lines):
        m = _match_weeks_line(line)
        if not m:
            continue
        name = lines[i - 2] if i >= 2 else ""
        teacher = lines[i - 1] if i >= 1 else ""
        # 班级课表模板里会带方括号：「软件质量保证与测试[讲课学时]」「[万鹏]」
        name = re.sub(r"\[[^\]]*\]\s*$", "", name).strip() or name
        teacher = teacher.strip().strip("[]（）()").strip()
        out.append({
            "name": name,
            "teacher": teacher,
            "weeks": normalize_weeks(m.group("weeks")),
            "periods": m.group("periods"),
            "room": lines[i + 1] if i + 1 < len(lines) else "",
        })
    return out


def looks_like_jiaowu(rows: list) -> bool:
    """是不是这种二维课表：前几行里有星期表头，且某一列是节次标签。"""
    for row in rows[:8]:
        heads = sum(1 for c in row if str(c).strip() in WEEKDAY_HEADERS)
        if heads >= 3:
            return True
    return False


def _merge_weeks(courses: list) -> tuple:
    """把同一格多门课的周次并起来。

    返回 (weeks, special)：weeks 是紧凑写法，空串表示每周；
    special 保存「单」「双」这类无法与数字合并的写法（同一格混用时以数字为准）。
    """
    weeks = set()
    every_week = False
    specials = set()
    for c in courses:
        w = c["weeks"]
        if not w:
            every_week = True
        elif w in ("单", "双"):
            specials.add(w)
        else:
            weeks |= parse_week_set(w)

    if every_week:
        return "", set()
    if weeks:
        return format_weeks(weeks), set()
    if len(specials) == 1:
        return next(iter(specials)), set()
    return "", set()


def parse_grid(rows: list) -> dict:
    """二维表 → {meta, cells, max_slot}。

    cells 的键是 (weekday, block)，值是 {weeks, note, courses}。
    """
    meta = {}
    header_row = -1
    for i, row in enumerate(rows[:8]):
        heads = sum(1 for c in row if str(c).strip() in WEEKDAY_HEADERS)
        if heads >= 3:
            header_row = i
            break
    if header_row < 0:
        raise ValueError("没找到星期表头，不像是教务导出的课表")

    # 元信息行（学年学期/班级/专业…）通常在标题行下面、表头上面。
    # 注意「班级人数:38」不能被当成班级名，所以班级要求紧跟冒号。
    for row in rows[:header_row]:
        for cell in row:
            text = str(cell or "")
            for key in ("班级", "学号", "姓名", "专业", "学年学期"):
                pattern = r"班级(?!人数)[：:]\s*([^\s　]+)" if key == "班级"                     else key + r"[：:]\s*([^\s　]+)"
                m = re.search(pattern, text)
                if m:
                    meta[key] = m.group(1).strip()

    # 班级课表模板里班级只出现在标题，例如「南昌航空大学 242013 班级课表」
    if "班级" not in meta:
        for row in rows[:header_row + 1]:
            for cell in row:
                text = str(cell or "")
                m = re.search(r"(?<!\d)(\d{4,8})\s*班", text)
                if m:
                    meta["班级"] = m.group(1)
                    break
            if "班级" in meta:
                break

    cells = {}
    max_slot = 0
    for row in rows[header_row + 1:]:
        if not row:
            continue
        slot = slot_from_label(row[0])
        if not slot:
            continue
        max_slot = max(max_slot, slot)
        for col, value in enumerate(row[1:8], start=1):     # 列 1..7 = 星期一..日
            courses = parse_cell(value)
            if not courses:
                continue
            weeks, _ = _merge_weeks(courses)
            names = []
            for c in courses:
                if c["name"] and c["name"] not in names:
                    names.append(c["name"])
            cells[(col, slot)] = {
                "weeks": weeks,
                "note": "、".join(names)[:60],
                "courses": courses,
            }
    return {"meta": meta, "cells": cells, "max_slot": max_slot,
            "header_row": header_row}


# ---------------------------------------------------------------- 读取文件

def read_rows(path: str | Path) -> tuple:
    """读 .xls / .xlsx / .csv，返回 (rows, meta)。

    rows 是二维的字符串表。教务系统导出的 .xls 是真正的 BIFF 格式，
    需要 xlrd；.xlsx 用 openpyxl。
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise ValueError(
                "读取 .xls 需要 xlrd 库，请先安装：pip install xlrd"
            ) from exc
        try:
            book = xlrd.open_workbook(str(path))
        except Exception as exc:
            raise ValueError(
                f"这个 .xls 文件读不了（可能已损坏，或其实不是 Excel 文件）：{exc}"
            ) from exc
        rows = []
        for sheet in book.sheets():
            for r in range(sheet.nrows):
                row = []
                for c in range(sheet.ncols):
                    v = sheet.cell_value(r, c)
                    if isinstance(v, float) and v == int(v):
                        v = int(v)
                    row.append(v)
                rows.append(row)
            break                     # 这种课表只有一张工作表
        return rows, {"source": path.name}

    if suffix == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(str(path), read_only=True, data_only=True)
        try:
            sheet = book.worksheets[0]
            rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        finally:
            book.close()
        return rows, {"source": path.name, "sheet": sheet.title}

    raise ValueError(f"不支持的文件类型：{suffix}。请提供 .xls / .xlsx / .csv。")


def parse_file(path: str | Path) -> dict:
    rows, extra = read_rows(path)
    if not looks_like_jiaowu(rows):
        raise ValueError("这个文件不像是教务导出的二维课表")
    result = parse_grid(rows)
    result.update(extra)
    return result
