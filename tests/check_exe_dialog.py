"""在打包后的真实 exe 里验证「导入名单」对话框能弹出。

开发环境里已经验证过对话框能开（tests/check_dialog.py），
但打包会改变运行环境（冻结的 pywebview、解压目录），所以必须对
真正要交付的那个 exe 再验一次。

做法：把探针临时写进界面文件，构建一个测试版 exe，启动后用 Win32
枚举窗口，确认文件对话框（窗口类 #32770）出现。检测到就关掉对话框，
然后恢复界面文件并清理。

这个测试要重新打包，耗时约 1 分钟，属于发布前检查。
运行：python tests/check_exe_dialog.py
"""
import ctypes
import ctypes.wintypes as w
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import build_exe  # noqa: E402

DIALOG_CLASS = "#32770"
WATCH_SECONDS = 45          # onefile 首启动要解压，给足时间

u = ctypes.windll.user32

PROBE = r"""
<script>
(function () {
  function go() {
    setTimeout(function () {
      try { window.pywebview.api.import_file(); } catch (e) {}
    }, 2500);
  }
  if (window.pywebview) go();
  else window.addEventListener("pywebviewready", go);
})();
</script>
"""


def windows_of(pid):
    out = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

    def cb(hwnd, _):
        p = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            n = u.GetWindowTextLengthW(hwnd)
            t = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, t, n + 1)
            out.append((hwnd, cls.value, t.value))
        return True

    u.EnumWindows(CB(cb), 0)
    return out


def pids_of(exe_name):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Process {Path(exe_name).stem} -ErrorAction SilentlyContinue | "
         f"Select-Object -ExpandProperty Id"],
        capture_output=True, text=True,
    )
    return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]


def main() -> int:
    tmp_dist = Path(tempfile.mkdtemp(prefix="rollcall_exe_"))
    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}",
              flush=True)

    try:
        # 探针写进临时文件再打包，不改动源码树里的 index.html。
        # （曾经靠临时改源文件实现，脚本被打断就会把探针留在源码里）
        src_file = ROOT / "web" / "index.html"
        probe_html = tmp_dist / "probe_index.html"
        probe_html.write_text(
            src_file.read_text(encoding="utf-8").replace("</body>", PROBE + "</body>"),
            encoding="utf-8",
        )

        print("打包测试版 exe（约 1 分钟）……", flush=True)
        exe = build_exe.build(tmp_dist, web_index=probe_html)
        print(f"产物：{exe}", flush=True)

        print("启动 exe 并等待文件对话框……", flush=True)
        subprocess.Popen([str(exe)], cwd=str(tmp_dist))

        deadline = time.time() + WATCH_SECONDS
        found = None
        while time.time() < deadline:
            for pid in pids_of(exe.name):
                for hwnd, cls, title in windows_of(pid):
                    if cls == DIALOG_CLASS:
                        found = (hwnd, cls, title)
                        break
                if found:
                    break
            if found:
                break
            time.sleep(0.5)

        if found:
            hwnd, cls, title = found
            print(f"  检测到对话框：类={cls} 标题={title!r}", flush=True)
            time.sleep(0.8)
            u.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
            time.sleep(0.8)

        print("\n" + "=" * 62, flush=True)
        check("打包后的 exe 能弹出文件对话框", found is not None,
              f"窗口类={found[1]} 标题={found[2]!r}" if found else "未检测到 #32770")

    finally:
        print("清理：结束 exe 进程、删除临时目录", flush=True)
        subprocess.run(["taskkill", "/F", "/IM", f"{build_exe.APP_NAME}.exe"],
                       capture_output=True)
        time.sleep(0.5)
        shutil.rmtree(tmp_dist, ignore_errors=True)

    print("=" * 62)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
