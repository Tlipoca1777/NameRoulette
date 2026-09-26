"""把 students.csv 导入 data.db，用于开发和演示。

真实名单到位后，直接在程序界面里「导入名单」即可，不必用这个脚本。
加 --reset 可先清空再导入。
"""
import sys
from pathlib import Path

import core
import db

HERE = Path(__file__).resolve().parent


def main():
    reset = "--reset" in sys.argv
    csv_path = HERE / "students.csv"
    if not csv_path.exists():
        raise SystemExit(f"找不到 {csv_path}")

    conn = db.connect()
    if reset:
        core.clear_students(conn)
        conn.execute("DELETE FROM draws")
        core.set_round(conn, 1)
        conn.commit()
        print("已清空原有数据")

    text = core.decode_bytes(csv_path.read_bytes())
    result = core.import_students(conn, text)
    print(f"导入 {result['added']} 人，跳过重复 {result['skipped']} 条")
    for w in result["warnings"]:
        print("  提示：", w)
    print("当前状态：", core.stats(conn))
    print("数据文件：", db.db_path())


if __name__ == "__main__":
    main()
