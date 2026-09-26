"""验证「导入名单」的文件对话框真的能弹出来。

这一步前后栽过两次：
  1. 过滤器写成 "Excel / CSV (*.xlsx;*.csv)"，含斜杠被 pywebview 判为非法，
     create_file_dialog 直接抛 ValueError；
  2. 抛出的异常被吞掉当作「用户取消」，界面上表现为点了按钮毫无反应。

所以这里不看返回值，而是从真实 pywebview 窗口里触发导入，
再用 Win32 枚举本进程的顶层窗口，确认文件对话框窗口（Win32 通用对话框，
窗口类 #32770）确实出现在了屏幕上。检测到之后用 WM_CLOSE 关掉它
（等同用户取消），避免测试卡住。

运行：python tests/check_dialog.py
"""
import ctypes
import ctypes.wintypes as w
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import core  # noqa: E402
import db  # noqa: E402
import webview  # noqa: E402

u = ctypes.windll.user32
MY_PID = os.getpid()
DIALOG_CLASS = "#32770"          # Win32 通用对话框（打开/保存文件都用它）
WATCH_SECONDS = 20

state = {"dialog_class": None, "dialog_title": None, "closed": False, "result": None}


def top_windows():
    """返回本进程所有可见顶层窗口的 (hwnd, class, title)。"""
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

    def cb(hwnd, _):
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == MY_PID and u.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            n = u.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, title, n + 1)
            out.append((hwnd, cls.value, title.value))
        return True

    u.EnumWindows(CB(cb), 0)
    return out


PROBE = r"""
<script>
(function () {
  function go() {
    setTimeout(function () {
      window.pywebview.api.import_file().then(function (r) {
        window.pywebview.api.ui_probe(JSON.stringify(r));
      }).catch(function (e) {
        window.pywebview.api.ui_probe(JSON.stringify({ error: String(e) }));
      });
    }, 1500);
  }
  if (window.pywebview) go();
  else window.addEventListener("pywebviewready", go);
})();
</script>
"""


class DialogApi(appmod.Api):
    def ui_probe(self, payload):
        try:
            state["result"] = json.loads(payload)
        except Exception:
            state["result"] = {"raw": str(payload)[:200]}
        return {"ok": True}


def worker(window):
    deadline = time.time() + WATCH_SECONDS
    while time.time() < deadline:
        for hwnd, cls, title in top_windows():
            if cls == DIALOG_CLASS:
                state["dialog_class"] = cls
                state["dialog_title"] = title
                time.sleep(1.0)
                u.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
                state["closed"] = True
                deadline = time.time() + 8
                break
        if state["closed"] and state["result"] is not None:
            break
        time.sleep(0.3)
    time.sleep(0.5)
    try:
        window.destroy()
    except Exception:
        pass


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="rollcall_dlg_")
    conn = db.connect(os.path.join(tmpdir, "t.db"))
    for i in range(1, 4):
        core.add_student(conn, f"学生{i}", f"2024{i:03d}", "一班")

    src = appmod.resource_path("web/index.html").read_text(encoding="utf-8")
    page = Path(tmpdir) / "probe.html"
    page.write_text(src.replace("</body>", PROBE + "</body>"), encoding="utf-8")

    api = DialogApi(conn=conn)
    window = webview.create_window("对话框测试", url=str(page), js_api=api,
                                   width=900, height=600)
    api._window = window
    webview.start(worker, (window,))

    print("=" * 62)
    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")

    check("文件对话框已弹出", state["dialog_class"] == DIALOG_CLASS,
          f"窗口类={state['dialog_class']} 标题={state['dialog_title']!r}")
    check("对话框是本进程的窗口（说明由程序主动打开）", state["dialog_class"] is not None)

    res = state["result"] or {}
    print(f"  [info] 取消后 import_file 返回：{res}")
    check("取消后返回 cancelled 而不是 error",
          res.get("cancelled") is True and "error" not in res, res)
    check("名单未被改动（取消不应导入任何数据）",
          core.stats(conn)["total"] == 3, core.stats(conn)["total"])

    print("=" * 62)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
