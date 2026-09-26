"""生成示例名单 students.csv（210 人，5 个班）。

仅用于开发阶段验证功能。真实名单由截图识别生成后覆盖此文件。
特意安排了两组同名不同学号的学生，用于验证同名同学能否被区分。
"""
import csv
import random
from pathlib import Path

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN_CHARS = list("伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英欣然宇轩浩宇梓涵子豪诗雨欣怡佳琪雨泽思远嘉豪梦琪俊杰雨欣晨曦若曦子涵心怡雅静雪婷文博天宇世豪紫涵梓萱雨桐芷若语嫣静怡可欣佳怡宇航昊天家豪雨萌慧敏晓燕建华志强秀兰")

CLASSES = [
    ("计算机2401", "2401"),
    ("计算机2402", "2402"),
    ("软件工程2401", "2403"),
    ("软件工程2402", "2404"),
    ("网络工程2401", "2405"),
]

PER_CLASS = 42
# 故意制造同名：这两组姓名会在两个班里各出现一次
DUPLICATE_NAMES = ["张伟", "李娜", "王鑫宇"]


def gen():
    random.seed(20240917)
    used = set()
    names = []

    def unique_name():
        for _ in range(500):
            n = random.choice(SURNAMES) + random.choice(GIVEN_CHARS)
            if random.random() < 0.7:
                n += random.choice(GIVEN_CHARS)
            if n not in used:
                used.add(n)
                return n
        # 极端情况下加个区分字
        n = random.choice(SURNAMES) + random.choice(GIVEN_CHARS) * 2
        used.add(n)
        return n

    rows = []
    for cls_name, code in CLASSES:
        for i in range(1, PER_CLASS + 1):
            name = unique_name()
            sid = f"2024{code}{i:02d}"
            rows.append({"姓名": name, "学号": sid, "班级": cls_name})
            names.append(name)

    # 注入同名同学：每个姓名在同两个班各出现一次
    for idx, dup in enumerate(DUPLICATE_NAMES):
        for cls_idx in range(2):
            row_idx = cls_idx * PER_CLASS + 4 + idx
            rows[row_idx]["姓名"] = dup

    return rows


def main():
    rows = gen()
    out = Path(__file__).resolve().parent / "students.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["姓名", "学号", "班级"])
        w.writeheader()
        w.writerows(rows)

    dups = {}
    for r in rows:
        dups.setdefault(r["姓名"], []).append(r["学号"])
    dup_names = {k: v for k, v in dups.items() if len(v) > 1}

    print(f"已生成 {out}  共 {len(rows)} 人  {len(CLASSES)} 个班")
    print(f"同名不同学号：{dup_names}")


if __name__ == "__main__":
    main()
