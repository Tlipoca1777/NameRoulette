"""JS 桥「迟到」时的启动健壮性测试。

真实背景：这个 bug 真的触发过——界面显示「无法连接后端」且卡死。
原因是 resolveShell 只等 pywebviewready 事件 3 秒，冷启动（onefile 首次解压、
机器偏慢）时桥来不及注入就误判，call() 直接抛错，而且没有重试。

这里用假桥模拟两种情况，验证界面最终都能正常初始化：
  A. 桥在 4.2 秒后才注入，但会派发 pywebviewready 事件（正常情形）
  B. 桥在 4.2 秒后才注入，且不派发任何事件（只能靠超时轮询发现）

判断依据：界面上的总人数应等于假桥给的值（7），而不是页面内置名单的 210。

运行：python tests/check_late_bridge.py
"""
import html as html_mod
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from check_layout import find_edge  # noqa: E402

FAKE_BRIDGE_TOTAL = 7

# 假桥：只实现启动阶段会用到的 state()
FAKE_API = """
function __makeBridge() {
  return {
    state: function () {
      return {
        stats: { total: %d, enabled: %d, remaining: %d, drawn: 0, disabled: 0, round: 1 },
        students: [], history: [], rounds: [], poolNames: [], dataDir: "假桥",
        pool: {
          counts: { available: %d, in_class: 0, soon: 0, drawn: 0, disabled: 0 },
          untalked: %d, reasons: {}, schedule_active: false,
          min_talk_minutes: 30, soon_detail: null,
          now: "2026-09-18 10:00:00", weekday: 5
        },
        now: "2026-09-18 10:00:00",
        currentBlock: null,
        scheduleConfigured: false,
        nextFree: null
      };
    }
  };
}
""" % (FAKE_BRIDGE_TOTAL, FAKE_BRIDGE_TOTAL, FAKE_BRIDGE_TOTAL,
       FAKE_BRIDGE_TOTAL, FAKE_BRIDGE_TOTAL)

PROBE_A = """
<script>
%s
setTimeout(function () {
  window.pywebview = { api: __makeBridge() };
  window.dispatchEvent(new Event("pywebviewready"));
}, 4200);
setTimeout(function () {
  var pre = document.createElement("pre"); pre.id = "BRIDGEOUT";
  pre.textContent = JSON.stringify({
    scenario: "A_桥迟到(4.2秒)并发事件",
    total: (document.getElementById("c-total") || {}).textContent,
    available: (document.getElementById("c-available") || {}).textContent,
    untalked: (document.getElementById("c-untalked") || {}).textContent,
    stage: (document.getElementById("display-name") || {}).textContent,
    hint: (document.getElementById("hint") || {}).textContent,
    emptyNote: document.querySelectorAll("#history-list .empty-note").length
  });
  document.body.appendChild(pre);
}, 9000);
</script>
""" % FAKE_API

PROBE_B = """
<script>
%s
setTimeout(function () {
  window.pywebview = { api: __makeBridge() };   // 只注入，不派发事件
}, 4200);
setTimeout(function () {
  var pre = document.createElement("pre"); pre.id = "BRIDGEOUT";
  pre.textContent = JSON.stringify({
    scenario: "B_桥迟到(4.2秒)且不发事件",
    total: (document.getElementById("c-total") || {}).textContent,
    available: (document.getElementById("c-available") || {}).textContent,
    untalked: (document.getElementById("c-untalked") || {}).textContent,
    stage: (document.getElementById("display-name") || {}).textContent,
    hint: (document.getElementById("hint") || {}).textContent,
    emptyNote: document.querySelectorAll("#history-list .empty-note").length
  });
  document.body.appendChild(pre);
}, 16000);
</script>
""" % FAKE_API


def run(edge: str, probe: str, label: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "late.html"
        src = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        page.write_text(src.replace("</body>", probe + "</body>"), encoding="utf-8")
        dump = Path(td) / "dump.html"
        with dump.open("w", encoding="utf-8") as f:
            subprocess.run(
                [edge, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=25000", "--window-size=1280,830",
                 "--dump-dom", page.as_uri()],
                stdout=f, stderr=subprocess.DEVNULL, timeout=180,
            )
        raw = dump.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'id="BRIDGEOUT">(.*?)</pre>', raw, re.S)
    if not m:
        raise RuntimeError(f"{label}：探针没有输出")
    return json.loads(html_mod.unescape(m.group(1)))


def main() -> int:
    edge = find_edge()
    if not edge:
        print("未找到 Microsoft Edge，跳过桥延迟测试")
        return 0

    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")

    for probe, label in ((PROBE_A, "A_桥迟到(4.2秒)并发事件"), (PROBE_B, "B_桥迟到(4.2秒)且不发事件")):
        print(f"\n【{label}】桥在 4.2 秒后才出现")
        try:
            d = run(edge, probe, label)
        except Exception as exc:
            check(f"{label}：能取到结果", False, repr(exc))
            continue

        check(f"{label}：界面用上了桥里的数据",
              str(d.get("total")) == str(FAKE_BRIDGE_TOTAL),
              f"总人数={d.get('total')}（桥给 {FAKE_BRIDGE_TOTAL}，内置名单是 210）")
        check(f"{label}：此刻可抽人数正确",
              str(d.get("available")) == str(FAKE_BRIDGE_TOTAL), d.get("available"))
        check(f"{label}：未谈人数正确",
              str(d.get("untalked")) == str(FAKE_BRIDGE_TOTAL), d.get("untalked"))
        check(f"{label}：大屏进入正常状态而非报错",
              d.get("stage") not in ("无法连接后端", "", None), f"大屏={d.get('stage')!r}")
        check(f"{label}：没有落到「无法连接后端」",
              "无法连接后端" not in str(d.get("stage")), d.get("stage"))
        check(f"{label}：历史显示空状态而非报错", d.get("emptyNote") == 1,
              d.get("emptyNote"))

    print("\n" + "=" * 64)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
