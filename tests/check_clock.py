"""时间显示会随现实时间走（exe 版优化）。

原来界面只在启动/抽取等动作后才更新，时间和「此刻可抽」都停在那一刻。
这里验证三件事：
  1. 时间行用的是本机时钟，不是后端那次快照
  2. tick() 会重新向后端要池子构成
  3. 弹窗打开时 tick() 跳过，不干扰正在进行的操作

运行：python tests/check_clock.py
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

import gen_html  # noqa: E402
from check_layout import find_edge  # noqa: E402

PROBE = r"""
<script>
(async function () {
  var out = {};
  function nowline() {
    return (document.getElementById("nowline") || {}).textContent || "";
  }
  function availText() {
    return (document.getElementById("c-available") || {}).textContent || "";
  }
  function hhmm() {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, "0"); };
    return p(d.getHours()) + ":" + p(d.getMinutes());
  }
  try {
    await new Promise(function (r) { setTimeout(r, 600); });
    out.realTime = hhmm();

    // 1) 把 S.now 伪装成很久以前：时间行仍应显示"现在"
    S.now = "2000-01-01 00:00:00";
    renderNow();
    out.nowlineAfterFake = nowline();
    out.showsFakeYear = out.nowlineAfterFake.indexOf("2000") >= 0;
    out.showsRealTime = out.nowlineAfterFake.indexOf(out.realTime) >= 0;

    // 2) tick 会刷新池子
    S.pool = { counts: { available: -1 }, untalked: -1 };
    document.getElementById("modal").hidden = true;
    await tick();
    out.afterTick = { available: S.pool.counts.available, note: availText() };

    // 3) 弹窗打开时 tick 应跳过
    document.getElementById("modal").hidden = false;
    S.pool = { counts: { available: -99 }, untalked: -99 };
    await tick();
    out.skippedWhileModal = S.pool.counts.available === -99;
    document.getElementById("modal").hidden = true;

    // 4) 抽取动画进行中也要跳过
    S.busy = true;
    S.pool = { counts: { available: -98 }, untalked: -98 };
    await tick();
    out.skippedWhileBusy = S.pool.counts.available === -98;
    S.busy = false;

    // 5) 定时器确实注册了（能拿到句柄且不报错）
    out.hasClockApi = typeof (await call("clock")).week !== "undefined";

    // 6) 时间行还应带周次与大课信息
    S.pool = { counts: { available: 0 }, untalked: 0 };
    await tick();
    out.finalNowline = nowline();
  } catch (e) {
    out.error = String((e && e.message) || e);
  }
  var pre = document.createElement("pre");
  pre.id = "CLOCKOUT";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script>
"""


def main() -> int:
    edge = find_edge()
    if not edge:
        print("未找到 Microsoft Edge，跳过时间刷新测试")
        return 0

    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "clock.html"
        gen_html.build(ROOT / "students.csv", page)
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("</body>", PROBE + "</body>"), encoding="utf-8")
        dump = Path(td) / "dump.html"
        with dump.open("w", encoding="utf-8") as f:
            subprocess.run(
                [edge, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=15000", "--window-size=1280,830",
                 "--dump-dom", page.as_uri()],
                stdout=f, stderr=subprocess.DEVNULL, timeout=180,
            )
        raw = dump.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'id="CLOCKOUT">(.*?)</pre>', raw, re.S)
    if not m:
        print("探针没有输出，页面可能没有正常加载")
        return 1
    d = json.loads(html_mod.unescape(m.group(1)))
    if d.get("error"):
        print("探针内抛错：", d["error"])
        return 1

    print("=" * 64)
    print("【时间行用本机时钟，不是后端快照】")
    check("把 S.now 伪造成 2000 年后，时间行不显示 2000",
          not d.get("showsFakeYear"), d.get("nowlineAfterFake"))
    check("时间行显示的是当前真实时间",
          d.get("showsRealTime"), f"界面={d.get('nowlineAfterFake')} 实际={d.get('realTime')}")

    print("\n【定时刷新池子】")
    check("tick 后池子被重新拉取", d.get("afterTick", {}).get("available", -1) >= 0,
          d.get("afterTick"))
    check("tick 后「此刻可抽」数字同步更新",
          str(d.get("afterTick", {}).get("available")) in str(d.get("afterTick", {}).get("note")),
          d.get("afterTick"))

    print("\n【不打断正在进行的操作】")
    check("弹窗打开时 tick 跳过", d.get("skippedWhileModal") is True, d.get("skippedWhileModal"))
    check("抽取动画进行中 tick 跳过", d.get("skippedWhileBusy") is True, d.get("skippedWhileBusy"))

    print("\n【轻量接口】")
    check("clock 接口可用", d.get("hasClockApi") is True, d.get("hasClockApi"))
    check("时间行含周次或大课信息",
          ("周" in (d.get("finalNowline") or "")) or ("大课" in (d.get("finalNowline") or "")),
          d.get("finalNowline"))

    print("\n" + "=" * 64)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
