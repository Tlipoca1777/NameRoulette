"""随机抽人 — 程序入口。

默认以 pywebview 原生窗口运行（推荐，不启 HTTP 服务、不触发防火墙弹窗）。
加 --http 参数则改用标准库 http.server + 默认浏览器，作为后备外壳。
两种外壳共用同一份 core.py 业务逻辑和 web/index.html 界面。

界面通过 web/index.html 里的 call() 适配层调用这里的方法，
因此换外壳不影响业务逻辑。
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

import core
import db

APP_TITLE = "随机抽人"
WINDOW_SIZE = (1200, 780)

# 文件对话框的过滤器。
# pywebview 用 ^([\w ]+)\(...\)$ 校验，描述部分只允许字母/数字/下划线/空格，
# 写成 "Excel / CSV (*.xlsx;*.csv)" 会因斜杠被判非法，对话框根本打不开
# （而且旧代码把异常吞了，表现成点了按钮没反应）。改动这里务必跑测试。
FILTER_TABLE = "Excel 或 CSV (*.xlsx;*.xls;*.csv)"
FILTER_ALL = "所有文件 (*.*)"
FILTER_BACKUP = "备份文件 (*.json)"
FILTER_GROUPS = {
    "表格导入": (FILTER_TABLE, FILTER_ALL),
    "备份导入": (FILTER_BACKUP, FILTER_ALL),
    "课表导入": (FILTER_TABLE, FILTER_ALL),
}


def resource_path(rel: str) -> Path:
    """定位随程序分发的资源文件。

    PyInstaller onefile 会把资源解压到临时目录 sys._MEIPASS。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    return Path(__file__).resolve().parent / rel


class Api:
    """暴露给界面的后端接口。所有方法都会被另一个线程调用，故用锁串行化。

    ⚠ 数据属性一律用下划线开头，不要改成公开名字。
    pywebview 启动时会用 util.get_functions() 递归遍历本对象的每个公开属性
    （见 webview/util.py:214），遇到非可调用对象就继续往里递归。曾把窗口对象
    存成公开的 self._window，于是它顺着 window.native 走进 WinForms/COM 对象图，
    无限递归（AccessibilityObject.Bounds.Empty.Empty...）并大量触发跨线程 COM 调用，
    导致启动时窗口「未响应」、日志刷屏。下划线开头的属性会被跳过。
    只有需要暴露给界面调用的方法才用公开名字。
    """

    def __init__(self, conn=None) -> None:
        self._conn = conn if conn is not None else db.connect()
        self._lock = threading.RLock()
        self._window = None
        self._gui = True         # False 表示运行在 HTTP 后备外壳下

    # ------------------------------------------------------------ 读取
    def state(self, ignore_schedule: bool = False) -> dict:
        with self._lock:
            now = datetime.now()
            pool = core.pool_status(self._conn, now=now,
                                    ignore_schedule=bool(ignore_schedule))
            pool.pop("available", None)      # 界面只要构成，名单另外给
            return {
                "stats": core.stats(self._conn),
                "students": core.list_students(self._conn),
                "history": core.history(self._conn),
                "rounds": core.all_rounds(self._conn),
                "poolNames": core.pool_names(self._conn),
                "dataDir": str(db.data_dir()),
                "pool": pool,
                "now": now.strftime("%Y-%m-%d %H:%M:%S"),
                "currentBlock": core.current_block(self._conn, now),
                "scheduleConfigured": core.schedule_configured(self._conn),
                "termStart": core.get_term_start(self._conn),
                "week": core.current_week(self._conn, now),
                "nextFree": core.next_free_hint(self._conn, now),
                "nextBlock": core.next_block(self._conn, now),
            }

    def clock(self) -> dict:
        """定时刷新用：只要时间和池子构成，不带 212 人的名单，
        避免每分钟都传一遍大对象。"""
        with self._lock:
            now = datetime.now()
            pool = core.pool_status(self._conn, now=now)
            pool.pop("available", None)
            return {
                "now": now.strftime("%Y-%m-%d %H:%M:%S"),
                "week": core.current_week(self._conn, now),
                "currentBlock": core.current_block(self._conn, now),
                "nextFree": core.next_free_hint(self._conn, now),
                "nextBlock": core.next_block(self._conn, now),
                "pool": pool,
            }

    # ------------------------------------------------------------ 抽取
    def draw(self, ignore_schedule: bool = False):
        with self._lock:
            return core.draw(self._conn, now=datetime.now(),
                             ignore_schedule=bool(ignore_schedule))

    def undo(self, draw_id):
        with self._lock:
            try:
                return core.undo(self._conn, int(draw_id))
            except ValueError as exc:
                raise Exception(str(exc)) from exc

    def start_new_term(self) -> dict:
        with self._lock:
            return core.start_new_term(self._conn)

    # ------------------------------------------------------------ 名单
    def add_student(self, name, sid="", cls=""):
        with self._lock:
            return core.add_student(self._conn, name, sid, cls)

    def update_student(self, student_id, field, value):
        """界面按字段逐个保存，这里映射到 core 的关键字参数。"""
        if field not in ("name", "sid", "cls"):
            raise Exception(f"不支持的字段：{field}")
        with self._lock:
            return core.update_student(self._conn, int(student_id), **{field: value})

    def set_enabled_many(self, ids, enabled):
        with self._lock:
            return core.set_enabled_many(self._conn, ids, bool(enabled))

    def delete_students(self, ids):
        with self._lock:
            return core.delete_students(self._conn, ids)

    def set_enabled(self, student_id, enabled):
        with self._lock:
            return core.set_enabled(self._conn, int(student_id), bool(enabled))

    def delete_student(self, student_id):
        with self._lock:
            try:
                return core.delete_student(self._conn, int(student_id))
            except ValueError as exc:
                raise Exception(str(exc)) from exc

    def clear_students(self) -> dict:
        with self._lock:
            return core.clear_students(self._conn)

    def import_text(self, text) -> dict:
        with self._lock:
            return core.import_students(self._conn, text or "")

    # ------------------------------------------------------------ 导入文件
    def import_file(self) -> dict:
        """选择一个或多个 Excel/CSV 文件并导入（.xlsx / .xls / .csv 都支持）。"""
        paths, err = self._pick_open_file(multiple=True)
        if err:
            return {"error": f"文件选择框打开失败（{err}）。可改用「粘贴文本导入」。",
                    "parsed": 0, "added": 0, "skipped": 0, "warnings": []}
        if not paths:
            return {"cancelled": True}

        total = {"parsed": 0, "added": 0, "skipped": 0, "warnings": [], "details": []}
        with self._lock:
            for path in paths:
                name = Path(path).name
                try:
                    r = core.import_students_from_file(self._conn, path)
                    total["parsed"] += r.get("parsed", 0)
                    total["added"] += r.get("added", 0)
                    total["skipped"] += r.get("skipped", 0)
                    total["warnings"].extend(r.get("warnings") or [])
                    total["details"].append(
                        {"name": name, "added": r.get("added", 0),
                         "skipped": r.get("skipped", 0)})
                except ValueError as exc:
                    total["details"].append({"name": name, "error": str(exc)})
                except Exception as exc:
                    total["details"].append({"name": name, "error": f"读取失败：{exc}"})
        total["files"] = len(paths)
        total["warnings"] = total["warnings"][:20]
        total["errors"] = [d for d in total["details"] if d.get("error")]
        if len(paths) == 1 and total["details"]:
            total["source"] = total["details"][0]["name"]
            if total["details"][0].get("error"):
                total["error"] = total["details"][0]["error"]
        return total

    # ------------------------------------------------------------ 导出
    def export_csv(self, round_no=None) -> dict:
        rnd = int(round_no) if round_no not in (None, "", "null") else None
        with self._lock:
            text = core.export_csv_text(self._conn, rnd)
            default = f"抽取记录_第{core.get_round(self._conn)}轮.csv"

        path, err = self._pick_save_file(default)
        if err:
            return {"error": f"保存对话框打开失败：{err}"}
        if not path:
            return {"cancelled": True}
        try:
            Path(path).write_text(text, encoding="utf-8")
            return {"path": path}
        except Exception as exc:
            return {"error": f"写入失败：{exc}"}

    def export_backup(self) -> dict:
        with self._lock:
            text = core.export_backup_text(self._conn)
        path, err = self._pick_save_file("随机抽人_备份.json")
        if err:
            return {"error": f"保存对话框打开失败：{err}"}
        if not path:
            return {"cancelled": True}
        try:
            Path(path).write_text(text, encoding="utf-8")
            return {"path": path}
        except Exception as exc:
            return {"error": f"写入失败：{exc}"}

    def import_backup(self) -> dict:
        path, err = self._pick_open_file(file_types=FILTER_GROUPS["备份导入"])
        if err:
            return {"error": f"文件选择框打开失败：{err}"}
        if not path:
            return {"cancelled": True}
        with self._lock:
            try:
                text = Path(path).read_text(encoding="utf-8")
                result = core.import_backup_text(self._conn, text)
                result["path"] = path
                return result
            except Exception as exc:
                return {"error": f"备份文件无法读取：{exc}"}

    # ------------------------------------------------------------ 课表与作息
    def schedule(self) -> dict:
        """课表页需要的全部数据。"""
        with self._lock:
            now = datetime.now()
            return {
                "grid": core.schedule_grid(self._conn),
                "exceptions": core.list_exceptions(self._conn),
                "minTalkMinutes": core.get_min_talk_minutes(self._conn),
                "targets": core.schedule_targets(self._conn),
                "now": now.strftime("%Y-%m-%d %H:%M:%S"),
                "weekday": now.isoweekday(),
                "week": core.current_week(self._conn, now),
                "termStart": core.get_term_start(self._conn),
                "currentBlock": core.current_block(self._conn, now),
            }

    def set_schedule(self, cls, weekday, block, has_class=True, note="", weeks=""):
        with self._lock:
            try:
                return core.set_schedule(self._conn, cls, weekday, block,
                                         bool(has_class), note, weeks)
            except ValueError as exc:
                raise Exception(str(exc)) from exc

    def set_block(self, idx, name=None, start=None, end=None):
        with self._lock:
            return core.set_block(self._conn, idx, name, start, end)

    def set_term_start(self, ymd):
        with self._lock:
            try:
                return core.set_term_start(self._conn, ymd)
            except ValueError as exc:
                raise Exception(str(exc)) from exc

    def set_min_talk_minutes(self, minutes):
        with self._lock:
            return core.set_min_talk_minutes(self._conn, minutes)

    def set_exception(self, student_id, weekday, block, has_class=True, note=""):
        with self._lock:
            return core.set_exception(self._conn, student_id, weekday, block,
                                      bool(has_class), note)

    def clear_exception(self, student_id, weekday, block):
        with self._lock:
            return core.clear_exception(self._conn, student_id, weekday, block)

    def export_schedule(self) -> dict:
        with self._lock:
            text = core.export_schedule_csv(self._conn)
        path, err = self._pick_save_file("课表.csv")
        if err:
            return {"error": f"保存对话框打开失败：{err}"}
        if not path:
            return {"cancelled": True}
        try:
            Path(path).write_text(text, encoding="utf-8")
            return {"path": path}
        except Exception as exc:
            return {"error": f"写入失败：{exc}"}

    def import_schedule(self, target: str = "class") -> dict:
        """导入一个或多个课表文件（.xls / .xlsx / .csv）。

        采用合并而非覆盖，避免手一滑清掉已录的课表。
        """
        paths, err = self._pick_open_file(file_types=FILTER_GROUPS["课表导入"],
                                         multiple=True)
        if err:
            return {"error": f"文件选择框打开失败（{err}）。"}
        if not paths:
            return {"cancelled": True}

        total = {"parsed": 0, "added": 0, "exceptions": 0, "errors": [],
                 "targets": [], "details": []}
        with self._lock:
            for path in paths:
                name = Path(path).name
                try:
                    r = core.import_schedule_from_file(self._conn, path,
                                                       target=target)
                except ValueError as exc:
                    total["details"].append({"name": name, "error": str(exc)})
                    continue
                except Exception as exc:
                    total["details"].append({"name": name, "error": f"读取失败：{exc}"})
                    continue
                if r.get("error"):
                    total["details"].append({"name": name, "error": r["error"]})
                    continue
                total["parsed"] += r.get("parsed", 0)
                total["added"] += r.get("added", 0)
                total["exceptions"] += r.get("exceptions", 0)
                total["errors"].extend(r.get("errors") or [])
                if r.get("target"):
                    total["targets"].append(f"{name} → {r['target']}")
                total["details"].append({"name": name, "added": r.get("added", 0),
                                         "target": r.get("target", "")})
        total["files"] = len(paths)
        total["errors"] = total["errors"][:20]
        total["target"] = "；".join(total["targets"]) if total["targets"] else ""
        # 单个文件失败时，把错误提到顶层，界面原来的提示逻辑就能用
        if len(paths) == 1 and total["details"] and total["details"][0].get("error"):
            total["error"] = total["details"][0]["error"]
        return total

    def schedule_targets(self) -> dict:
        with self._lock:
            return core.schedule_targets(self._conn)

    def clear_student_schedule(self, student_id=None):
        with self._lock:
            return core.clear_student_schedule(self._conn, student_id)

    def clear_schedule(self, with_exceptions: bool = True):
        with self._lock:
            return core.clear_schedule(self._conn, bool(with_exceptions))

    # ------------------------------------------------------------ 文件对话框
    # 两个 helper 都返回 (路径, 错误信息)：
    # None 表示用户取消，错误字符串表示对话框本身失败。
    # 必须区分这两种情况——否则对话框出问题时界面只当作用户取消，
    # 表现为「点了按钮毫无反应」，无从排查。

    def _pick_open_file(self, file_types=None, multiple=False):
        """返回 (路径列表, 错误)。multiple=True 时支持一次选多个文件。"""
        file_types = file_types or FILTER_GROUPS["表格导入"]
        if not self._gui or self._window is None:
            # HTTP 后备外壳下没有原生对话框
            return None, None
        try:
            import webview

            picked = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=bool(multiple),
                file_types=file_types,
            )
            if not picked:
                return None, None
            if isinstance(picked, (list, tuple)):
                return list(picked), None
            return [picked], None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def _pick_save_file(self, default_name: str):
        if not self._gui or self._window is None:
            # 无对话框时落到程序目录的 导出 文件夹
            out_dir = db.data_dir() / "导出"
            out_dir.mkdir(parents=True, exist_ok=True)
            return str(out_dir / default_name), None
        try:
            import webview

            picked = self._window.create_file_dialog(
                webview.SAVE_DIALOG, save_filename=default_name
            )
            if not picked:
                return None, None
            return (picked if isinstance(picked, str) else picked[0]), None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"


# ================================================================ 外壳一：pywebview

def run_gui() -> None:
    import webview

    api = Api()
    index = resource_path("web/index.html")
    if not index.exists():
        raise SystemExit(f"界面文件缺失：{index}")

    window = webview.create_window(
        APP_TITLE,
        url=str(index),
        js_api=api,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=(940, 640),
    )
    api._window = window
    webview.start()


# ================================================================ 外壳二：HTTP 后备

def run_http(port: int = 8731) -> None:
    """后备外壳：标准库 HTTP 服务 + 默认浏览器。

    仅在 pywebview 不可用时使用。只监听 127.0.0.1，不对外网开放。
    """
    import webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    api = Api()
    api._gui = False
    index = resource_path("web/index.html")

    # 注入外壳标记，让界面不必靠探测就知道该走 HTTP 分支
    index_html = index.read_bytes()
    marker = b'<script>window.__SHELL__="http";</script>'
    head = b"<head>"
    pos = index_html.find(head)
    if pos != -1:
        cut = pos + len(head)
        index_html = index_html[:cut] + marker + index_html[cut:]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):        # 静默访问日志
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(index_html, "text/html; charset=utf-8")
            else:
                self._send(b"not found", "text/plain; charset=utf-8", 404)

        def do_POST(self):
            name = self.path.rstrip("/").rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                args = json.loads(raw.decode("utf-8")) if raw else []
            except json.JSONDecodeError:
                args = []

            fn = getattr(api, name, None)
            if fn is None or name.startswith("_") or not callable(fn):
                self._send(b'{"error":"unknown method"}', "application/json", 404)
                return
            try:
                result = fn(*args)
                body = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
                self._send(body, "application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(body, "application/json; charset=utf-8", 500)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"{APP_TITLE} 已启动：{url}")
    print(f"数据文件：{db.db_path()}")
    print("关闭此窗口即结束程序。")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> None:
    if "--http" in sys.argv:
        run_http()
        return
    try:
        run_gui()
    except Exception as exc:
        print(f"原生窗口启动失败（{exc}），改用浏览器模式……", file=sys.stderr)
        run_http()


if __name__ == "__main__":
    main()
