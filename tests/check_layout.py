"""布局与可点性检查：确认页面切换后，用户真的能看到并点到目标按钮。

这类问题查不出来是因为断言方式错了：
CSS 优先级导致 hidden 失效时，元素仍在 DOM 里、textContent 也正常，
数元素个数一切正常，但页面被挤到视口外面、根本点不到。

所以这里必须测三样东西：
  1. 可见性 —— 非活动页的 computed display 必须是 none
  2. 坐标   —— 目标按钮的矩形必须完整落在视口内
  3. 命中测试 —— 按钮中心点 elementFromPoint 命中的必须是按钮本身

用无头 Edge 渲染生成的网页版来测。两个版本共用同一份 CSS 与界面代码，
因此这里通过即代表 exe 版同样通过。

运行：python tests/check_layout.py
"""
import html as html_mod
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import gen_html  # noqa: E402

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

PROBE = r"""
<script>
(function () {
  function rect(el) { return el.getBoundingClientRect(); }
  function within(el, vh) {
    var r = rect(el);
    return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= vh + 1;
  }
  function hitOk(el) {
    var r = rect(el);
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    var hit = document.elementFromPoint(cx, cy);
    if (!hit) return "null";
    if (hit === el || el.contains(hit)) return "self";
    return hit.tagName + (hit.id ? "#" + hit.id : "") + (hit.className ? "." + hit.className : "");
  }
  function state(tag) {
    var vh = window.innerHeight;
    var draw = document.getElementById("view-draw");
    var roster = document.getElementById("view-roster");
    var importBtn = document.getElementById("btn-import-file");
    var drawBtn = document.getElementById("btn-draw");
    var table = document.querySelector(".table-wrap");
    return {
      tag: tag,
      viewportH: vh,
      drawDisplay: getComputedStyle(draw).display,
      rosterDisplay: getComputedStyle(roster).display,
      drawHiddenAttr: draw.hasAttribute("hidden"),
      rosterHiddenAttr: roster.hasAttribute("hidden"),
      importBtnVisible: within(importBtn, vh),
      importBtnTop: Math.round(rect(importBtn).top),
      importBtnHit: hitOk(importBtn),
      drawBtnHit: hitOk(drawBtn),
      rosterRows: document.querySelectorAll("#roster-body tr").length,
      tableVisible: table ? table.getBoundingClientRect().height > 50 : false
    };
  }
  var out = [];
  var tabs = document.querySelectorAll(".tabs button");
  function switchTo(i) { tabs[i].click(); }
  function finish() {
    var pre = document.createElement("pre");
    pre.id = "DIAGOUT";
    pre.textContent = JSON.stringify({
      tabCount: tabs.length, steps: out,
      errors: (window.__ERRORS__ || []).slice(0, 5)
    });
    document.body.appendChild(pre);
  }
  window.__ERRORS__ = [];
  window.addEventListener("error", function (e) {
    (window.__ERRORS__ = window.__ERRORS__ || []).push(String(e.message));
  });
  setTimeout(function () {
    out.push(state("初始(抽取页)"));
    switchTo(1);
    setTimeout(function () {
      out.push(state("切到名单管理"));
      switchTo(0);
      setTimeout(function () {
        out.push(state("切回抽取"));
        switchTo(1);
        setTimeout(function () { out.push(state("再切到名单管理")); finish(); }, 350);
      }, 350);
    }, 350);
  }, 1200);
})();
</script>
"""


def find_edge():
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def run_probe(edge: str, url: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        dump = Path(td) / "dump.html"
        with dump.open("w", encoding="utf-8") as f:
            subprocess.run(
                [edge, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=10000", "--window-size=1280,830",
                 "--dump-dom", url],
                stdout=f, stderr=subprocess.DEVNULL, timeout=120,
            )
        raw = dump.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'id="DIAGOUT">(.*?)</pre>', raw, re.S)
    if not m:
        raise RuntimeError("探针没有输出，页面可能没有正常加载")
    return json.loads(html_mod.unescape(m.group(1)))


def main() -> int:
    edge = find_edge()
    if not edge:
        print("未找到 Microsoft Edge，跳过布局检查")
        return 0

    roster = ROOT / "students.csv"
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "layout.html"
        gen_html.build(roster, page)
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("</body>", PROBE + "</body>"), encoding="utf-8")
        data = run_probe(edge, page.as_uri())

    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")

    steps = {s["tag"]: s for s in data["steps"]}
    print("=" * 64)
    check("探针捕获到全部 4 个状态", len(data["steps"]) == 4,
          f"{[s['tag'] for s in data['steps']]}")
    check("页签数量为 3（抽取/名单管理/课表）", data["tabCount"] == 3, data["tabCount"])
    check("页面无 JS 报错", not data["errors"], data["errors"])

    print("\n【初始状态】应停在抽取页，名单页不可见")
    s = steps.get("初始(抽取页)")
    if s:
        check("抽取页可见", s["drawDisplay"] != "none", s["drawDisplay"])
        check("名单页不可见", s["rosterDisplay"] == "none", s["rosterDisplay"])
        check("抽取按钮可命中", s["drawBtnHit"] == "self", s["drawBtnHit"])

    print("\n【切到名单管理】必须真的切过去且按钮可点")
    s = steps.get("切到名单管理")
    if s:
        check("抽取页已隐藏", s["drawDisplay"] == "none",
              f"display={s['drawDisplay']} hidden属性={s['drawHiddenAttr']}")
        check("名单页已显示", s["rosterDisplay"] != "none", s["rosterDisplay"])
        check("名单页有实际高度", s["tableVisible"], "表格高度需 > 50px")
        check("导入按钮在视口内", s["importBtnVisible"],
              f"按钮 top={s['importBtnTop']} 视口高={s['viewportH']}")
        check("导入按钮可被点中", s["importBtnHit"] == "self",
              f"命中={s['importBtnHit']}")
        check("名单表格已渲染", s["rosterRows"] > 0, f"行数={s['rosterRows']}")

    print("\n【切回抽取页】名单页必须让位")
    s = steps.get("切回抽取")
    if s:
        check("抽取页恢复显示", s["drawDisplay"] != "none", s["drawDisplay"])
        check("名单页已隐藏", s["rosterDisplay"] == "none", s["rosterDisplay"])
        check("抽取按钮可命中", s["drawBtnHit"] == "self", s["drawBtnHit"])

    print("\n【再次切到名单管理】来回切换要保持一致")
    s = steps.get("再切到名单管理")
    if s:
        check("抽取页再次隐藏", s["drawDisplay"] == "none", s["drawDisplay"])
        check("导入按钮仍可点中", s["importBtnHit"] == "self", s["importBtnHit"])

    print("\n" + "=" * 64)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
