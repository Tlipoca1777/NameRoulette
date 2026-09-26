"""打包成单文件 exe。

用法：python build_exe.py

产物：发布/随机抽人.exe

说明：
- onefile 模式产出单个文件，方便发给别人；首次启动需解压，约 2-4 秒。
- windowed 模式不显示黑色控制台窗口。
- pywebview 依赖 pythonnet（.NET/WebView2），必须显式收集，
  否则打包后运行会报找不到 clr。
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_NAME = "随机抽人"
DIST = HERE / "发布"


def pyinstaller_args(dist: Path, web_index: Path | None = None) -> list:
    """打包参数。tests/check_exe_dialog.py 会复用，避免两处参数走偏。

    web_index 可指定要打包的界面文件，默认用 web/index.html。
    测试要注入探针时可以传一份临时文件，从而不必改动源码树。
    """
    import os

    web_index = web_index or (HERE / "web" / "index.html")
    if not web_index.exists():
        raise SystemExit(f"界面文件缺失：{web_index}")

    return [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", str(dist),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        # 界面文件必须一起打包
        "--add-data", f"{web_index}{os.pathsep}web",
        # pywebview / pythonnet 的隐藏依赖
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--hidden-import", "clr",
        "--hidden-import", "pythonnet",
        "--hidden-import", "openpyxl",
        "--hidden-import", "xlrd",      # 读教务导出的 .xls
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "webview.platforms.winforms",
        # 排除用不到的大块头，缩小体积
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PySide2",
        "--exclude-module", "test",
        "--noupx",                       # UPX 压缩容易被杀软误报
        str(HERE / "app.py"),
    ]


def build(dist: Path | None = None, web_index: Path | None = None) -> Path:
    """执行打包，返回 exe 路径。"""
    dist = dist or DIST
    print(f"开始打包到 {dist} ……")
    result = subprocess.run(pyinstaller_args(dist, web_index), cwd=str(HERE))
    if result.returncode != 0:
        raise SystemExit(f"打包失败，返回码：{result.returncode}")
    return dist / f"{APP_NAME}.exe"


def make_sample_xlsx(csv_path: Path, out_path: Path):
    """把示例 CSV 转成 xlsx，方便验证 Excel 导入。缺 openpyxl 就跳过。"""
    try:
        from openpyxl import Workbook
    except ImportError:
        return None

    import csv as csvmod

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csvmod.reader(f))
    if not rows:
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = "学生名单"
    # 首行放个标题，用来演示「标题行在表头之上」也能正确导入
    first_cls = rows[1][2] if len(rows) > 1 and len(rows[1]) > 2 else ""
    ws.append([f"{first_cls}等班级学生名单"])
    for row in rows:
        ws.append(row)
    wb.save(out_path)
    return out_path


def make_sample_schedule_xlsx(out_path: Path):
    """生成一份示例课表 xlsx，方便立刻验证课表导入。"""
    try:
        from openpyxl import Workbook
    except ImportError:
        return None

    # 一行一个课时：班级, 星期, 大课, 周次, 课程
    # 周次故意各不相同，用来演示「时段有时有课、有时没课」
    plan = [
        ("计算机2401", 1, 1, "1-16", "高等数学"),
        ("计算机2401", 2, 2, "1-16", "大学英语"),
        ("计算机2401", 3, 3, "1-8", "数据结构"),          # 只有前 8 周有课
        ("计算机2401", 5, 1, "9-16", "线性代数"),          # 后半学期才开始
        ("计算机2402", 1, 3, "1-16", "程序设计基础"),
        ("计算机2402", 3, 1, "1-8,10-16", "高等数学"),     # 第 9 周停一次
        ("计算机2402", 4, 4, "双", "体育"),                # 双周
        ("计算机2402", 5, 2, "1-16", "大学英语"),
        ("软件工程2401", 1, 2, "1-16", "离散数学"),
        ("软件工程2401", 2, 1, "1-16", "程序设计基础"),
        ("软件工程2401", 4, 3, "1-12", "软件工程导论"),
        ("软件工程2401", 5, 4, "单", "体育"),              # 单周
        ("软件工程2402", 2, 3, "1-16", "离散数学"),
        ("软件工程2402", 3, 2, "1-16", "大学物理"),
        ("软件工程2402", 4, 1, "1-16", "程序设计基础"),
        ("网络工程2401", 1, 4, "1-16", "计算机网络"),
        ("网络工程2401", 3, 4, "1-16", "大学物理"),
        ("网络工程2401", 5, 3, "1-16", "体育"),
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "课程表"
    # 首行放个标题，演示「标题行在表头之上」也能正确导入
    ws.append(["2026-2027 学年第一学期课程表", None, None, None, None])
    ws.append(["班级", "星期", "大课", "周次", "课程"])
    for cls, wd, blk, weeks, course in plan:
        ws.append([cls, wd, blk, weeks, course])
    # 一条个别学生例外：全班有课但他不上（周次列留空）
    ws.append(["@陈雨", 3, 3, "", "这节不上｜选修课冲突"])
    wb.save(out_path)
    return out_path


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller，请先运行：")
        print("  python -m pip install pyinstaller "
              "-i https://pypi.tuna.tsinghua.edu.cn/simple")
        return 1

    exe = build(DIST)

    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\n打包完成：{exe}  （{size_mb:.1f} MB）")

    # 把真实名单一起放在发布目录，方便首次导入
    roster = HERE / "students.csv"
    if roster.exists():
        shutil.copy2(roster, DIST / "示例名单_210人.csv")
        print(f"已附带示例名单：{DIST / '示例名单_210人.csv'}")
        xlsx = make_sample_xlsx(roster, DIST / "示例名单_210人.xlsx")
        if xlsx:
            print(f"已附带示例 Excel：{xlsx}")
        sched = make_sample_schedule_xlsx(DIST / "示例课表.xlsx")
        if sched:
            print(f"已附带示例课表：{sched}")

    readme = HERE / "README.md"
    if readme.exists():
        shutil.copy2(readme, DIST / "使用说明.md")

    print("\n发布目录内容：")
    for item in sorted(DIST.iterdir()):
        print("  ", item.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
