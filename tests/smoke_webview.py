"""pywebview 桥接冒烟测试。

验证 JS 能调用 Python 接口并把结果回传，这是整个架构的地基。
带看门狗超时，桥接失败时不会卡死。
"""
import sys
import threading

import webview

TIMEOUT = 25

HTML = """
<html><body><div id="out">waiting</div>
<script>
function run() {
  window.pywebview.api.ping().then(function (r) {
    return window.pywebview.api.report(r + "|bridge-ok");
  }).catch(function (e) {
    window.pywebview.api.report("BRIDGE-FAIL:" + e);
  });
}
if (window.pywebview) { run(); }
else { window.addEventListener("pywebviewready", run); }
</script></body></html>
"""


class Api:
    def __init__(self):
        self.result = None
        self.window = None

    def ping(self):
        return "pong"

    def report(self, value):
        self.result = value
        self.window.destroy()


def main():
    api = Api()
    window = webview.create_window("smoke", html=HTML, js_api=api, width=360, height=240)
    api.window = window

    def watchdog():
        if api.result is None:
            print("RESULT: TIMEOUT — 桥接无响应")
            try:
                window.destroy()
            except Exception:
                pass
            sys.stdout.flush()

    timer = threading.Timer(TIMEOUT, watchdog)
    timer.daemon = True
    timer.start()

    webview.start()
    timer.cancel()

    if api.result is None:
        print("RESULT: 窗口已关闭但未收到回调")
    else:
        print("RESULT:", api.result)


if __name__ == "__main__":
    main()
