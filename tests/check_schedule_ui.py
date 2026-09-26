"""课表页的界面测试（真实浏览器）。

覆盖三类容易出错的地方：
  1. 可见性与可点性 —— 新加的 #view-schedule 又设了 display:flex，
     如果忘了把 [hidden] 规则补上，三个页面会同时显示（之前踩过这个坑）；
  2. 交互 —— 点方格能否真的切换并落盘；
  3. 说明文案 —— 池子为空时必须说清是"在上课"还是"都谈过了"。

运行：python tests/check_schedule_ui.py
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
  function rect(el) { return el.getBoundingClientRect(); }
  function hit(el) {
    var r = rect(el);
    var h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!h) return "null";
    return (h === el || el.contains(h)) ? "self" : h.tagName + (h.id ? "#" + h.id : "");
  }
  function vis(el, vh) {
    var r = rect(el);
    return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= vh + 1;
  }
  function snap(tag) {
    var vh = window.innerHeight;
    return {
      tag: tag,
      drawDisp: getComputedStyle(document.getElementById("view-draw")).display,
      rosterDisp: getComputedStyle(document.getElementById("view-roster")).display,
      schedDisp: getComputedStyle(document.getElementById("view-schedule")).display,
      schedImportVisible: vis(document.getElementById("btn-sched-import"), vh),
      schedImportHit: hit(document.getElementById("btn-sched-import")),
      gridRows: document.querySelectorAll("#sched-grid tbody tr").length,
      gridCells: document.querySelectorAll("#sched-grid td.cell").length,
      blockRows: document.querySelectorAll("#blocks-table tbody tr").length,
      poolNote: (document.getElementById("pool-note") || {}).textContent || "",
      cAvail: (document.getElementById("c-available") || {}).textContent,
      cUntalked: (document.getElementById("c-untalked") || {}).textContent,
      nowline: (document.getElementById("nowline") || {}).textContent || "",
      weekHint: (document.getElementById("week-hint") || {}).textContent || "",
      schedOwner: (document.getElementById("sched-owner") || {}).textContent || "",
      blockHeads: [].map.call(document.querySelectorAll("#sched-grid thead tr:nth-child(2) th"),
                              function (th) { return th.textContent.trim(); }),
      schedOwner: (document.getElementById("sched-owner") || {}).textContent || "",
      blockHeads: [].map.call(document.querySelectorAll("#sched-grid thead tr:nth-child(2) th"),
                              function (th) { return th.textContent.trim(); }),
      termStartVal: (document.getElementById("inp-term-start") || {}).value,
      overrideHidden: document.getElementById("override-wrap").hidden,
      errored: document.getElementById("display-name").textContent === "无法连接后端"
    };
  }
  function go(i) { document.querySelectorAll(".tabs button")[i].click(); }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  try {
    out.tabCount = document.querySelectorAll(".tabs button").length;
    out.steps = [];
    out.steps.push(snap("初始(抽取页)"));

    // 必须用名单里真实存在的班级，否则设了课表也影响不到任何人
    var st0 = await call("state");
    var realCls = {};
    st0.students.forEach(function (s) { if (s.cls) realCls[s.cls] = (realCls[s.cls] || 0) + 1; });
    var allCls = (await call("schedule")).grid.classes;
    var cls0 = allCls.filter(function (c) { return realCls[c]; })[0];
    out.cls0 = cls0;
    out.cls0Count = realCls[cls0] || 0;

    await call("set_schedule", cls0, 3, 1, true, "高等数学");
    await call("set_min_talk_minutes", 30);
    await refresh();

    // 学期开始日期设为 14 天前 → 应算出第 3 周
    var d0 = new Date();
    d0.setDate(d0.getDate() - 14);
    var pad = function (n) { return String(n).padStart(2, "0"); };
    var startYmd = d0.getFullYear() + "-" + pad(d0.getMonth() + 1) + "-" + pad(d0.getDate());
    await call("set_term_start", startYmd);
    await refresh();
    out.weekDisplay = (document.getElementById("nowline") || {}).textContent || "";
    out.expectWeek = Math.floor(14 / 7) + 1;

    out.recorded = await call("schedule").then(function (d) {
      var c = d.grid.cells[cls0 + "|3|1"];
      return c ? c.note : null;
    });

    go(1); await wait(400);
    out.steps.push(snap("名单管理页"));

    go(2); await wait(900);
    out.steps.push(snap("课表页"));

    // 点第一行第一列的方格，看它是否切换并落盘
    var cell = document.querySelector("#sched-grid td.cell");
    var before = cell ? cell.classList.contains("on") : null;
    if (cell) { cell.click(); await wait(900); }
    var cell2 = document.querySelector("#sched-grid td.cell");
    out.cellToggle = {
      before: before,
      after: cell2 ? cell2.classList.contains("on") : null,
      persisted: await call("schedule").then(function (d) {
        return Object.keys(d.grid.cells).length;
      })
    };

    await call("clear_schedule", true);

    // 把所有班级的所有时段都填上有课 → 此刻必然一个人都抽不出来
    // 必须把「所有大课」都设成有课，否则当前时刻之后若还有没占用的时段，
    // 就会被判成空闲（这个测试曾在傍晚跑时因此失败）。
    var allBlocks = (await call("schedule")).grid.blocks.map(function (b) { return b.idx; });
    out.blockCount = allBlocks.length;
    var n = 0;
    for (var ci = 0; ci < allCls.length; ci++) {
      for (var d = 1; d <= 7; d++) {
        for (var bi = 0; bi < allBlocks.length; bi++) {
          await call("set_schedule", allCls[ci], d, allBlocks[bi], true, "");
          n++;
        }
      }
    }
    out.busyCellsSet = n;
    // 只在有课区间内，"全部有课"才会让此刻可抽变成 0；课间时下节课还很远就算空闲。
    // 把最短可谈时长调得很大，任何"后面还有课"的时段都算不可用，断言就不依赖运行时刻了。
    await call("set_min_talk_minutes", 99999);
    await refresh();
    var dbg = await call("state");
    out.dbg = {
      blockCount: allBlocks.length,
      schedCells: Object.keys((await call("schedule")).grid.cells).length,
      minTalk: dbg.pool.min_talk_minutes,
      schedActive: dbg.pool.schedule_active,
      configured: dbg.scheduleConfigured,
      counts: dbg.pool.counts,
      weekday: dbg.pool.weekday,
      week: dbg.week,
      now: dbg.pool.now,
      currentBlock: dbg.currentBlock ? dbg.currentBlock.name : null,
      ignoreChecked: document.getElementById("chk-ignore").checked
    };
    go(0); await wait(400);
    out.allBusy = snap("全部有课时的抽取页");

    // 恢复到正常值，再验证忽略课表
    await call("set_min_talk_minutes", 30);
    document.getElementById("chk-ignore").checked = true;
    await refresh();
    await wait(300);
    out.ignored = snap("勾选忽略课表后");

    // 撤掉课表，应退化为"不排除任何人"
    await call("clear_schedule", true);
    document.getElementById("chk-ignore").checked = false;
    await refresh();
    await wait(300);
    out.cleared = snap("清空课表后");
    out.configuredAfterClear = await call("schedule").then(function (d) {
      return d.grid.configured;
    });
  } catch (e) {
    out.error = String((e && e.message) || e);
  }
  var pre = document.createElement("pre");
  pre.id = "SCHEDOUT";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script>
"""


def main() -> int:
    edge = find_edge()
    if not edge:
        print("未找到 Microsoft Edge，跳过课表界面测试")
        return 0

    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "sched.html"
        gen_html.build(ROOT / "students.csv", page)   # 示例名单里有多个班级
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("</body>", PROBE + "</body>"), encoding="utf-8")

        dump = Path(td) / "dump.html"
        with dump.open("w", encoding="utf-8") as f:
            subprocess.run(
                [edge, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=20000", "--window-size=1400,900",
                 "--dump-dom", page.as_uri()],
                stdout=f, stderr=subprocess.DEVNULL, timeout=240,
            )
        raw = dump.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'id="SCHEDOUT">(.*?)</pre>', raw, re.S)
    if not m:
        print("探针没有输出，页面可能没有正常加载")
        return 1
    d = json.loads(html_mod.unescape(m.group(1)))
    if d.get("error"):
        print("探针内抛错：", d["error"])
        return 1

    steps = {s["tag"]: s for s in d["steps"]}

    print("=" * 64)
    check("页签数量为 3", d.get("tabCount") == 3, d.get("tabCount"))

    print("\n【初始：停在抽取页，另两页必须真的隐藏】")
    s = steps.get("初始(抽取页)")
    if s:
        check("抽取页可见", s["drawDisp"] != "none", s["drawDisp"])
        check("名单页隐藏", s["rosterDisp"] == "none", s["rosterDisp"])
        check("课表页隐藏", s["schedDisp"] == "none", s["schedDisp"])
        check("顶栏显示此刻可抽/未谈", s["cAvail"] not in (None, "", "–")
              and s["cUntalked"] not in (None, "", "–"),
              f"可抽={s['cAvail']} 未谈={s['cUntalked']}")
        check("显示了当前时间", "现在" in (s["nowline"] or ""), s["nowline"])

    print("\n【切到名单管理】")
    s = steps.get("名单管理页")
    if s:
        check("抽取页隐藏", s["drawDisp"] == "none", s["drawDisp"])
        check("名单页显示", s["rosterDisp"] != "none", s["rosterDisp"])
        check("课表页仍隐藏", s["schedDisp"] == "none", s["schedDisp"])

    print("\n【切到课表页】")
    s = steps.get("课表页")
    if s:
        check("抽取页隐藏", s["drawDisp"] == "none", s["drawDisp"])
        check("名单页隐藏", s["rosterDisp"] == "none", s["rosterDisp"])
        check("课表页显示", s["schedDisp"] != "none", s["schedDisp"])
        check("导入课表按钮在视口内", s["schedImportVisible"], "按钮应可见")
        check("导入课表按钮可被点中", s["schedImportHit"] == "self", s["schedImportHit"])
        nblk = d.get("dbg", {}).get("blockCount", 6)
        check(f"作息表渲染出全部 {nblk} 个大课", s["blockRows"] == nblk,
              f"渲染 {s['blockRows']} 行")
        check("课表网格渲染出班级行", s["gridRows"] > 0, f"行数={s['gridRows']}")
        check(f"课表网格列数 = 7 天 × {nblk} 大课",
              s["gridCells"] == s["gridRows"] * 7 * nblk,
              f"格子数={s['gridCells']} 行数={s['gridRows']} × {7 * nblk}")
        check("录进去的课程名读得回来",
              d.get("recorded") == "高等数学", d.get("recorded"))
        check("示例名单里有可用的真实班级", bool(d.get("cls0")), d.get("cls0"))

    print("\n【课表归属显示与列头】")
    s2 = steps.get("课表页") or {}
    check("课表页显示已录班级（不再自相矛盾地显示「空」）",
          (d.get("cls0") or "") in (s2.get("schedOwner") or ""),
          s2.get("schedOwner"))
    heads = s2.get("blockHeads") or []
    check("白天大课列头是 上①②下①②",
          heads[:4] == ["上①", "上②", "下①", "下②"], heads[:6])
    check("晚间大课列头是 晚①②（不是 5、6）",
          len(heads) >= 6 and heads[4] == "晚①" and heads[5] == "晚②", heads[4:6])
    print("\n【教学周显示与学期开始日期】")
    wk = d.get("expectWeek")
    check("顶栏那行显示「第 N 周」",
          ("第 " + str(wk) + " 周") in (d.get("weekDisplay") or ""),
          d.get("weekDisplay"))
    s = steps.get("课表页") or {}
    check("课表页显示当前是第几周",
          ("第 " + str(wk) + " 周") in (s.get("weekHint") or ""), s.get("weekHint"))
    check("学期开始日期已回填到输入框", bool(s.get("termStartVal")),
          s.get("termStartVal"))

    print("\n【点方格能否切换并落盘】")
    ct = d.get("cellToggle") or {}
    check("点击前该格是未选中", ct.get("before") is False, ct.get("before"))
    check("点击后变成选中", ct.get("after") is True, ct.get("after"))
    check("后端已持久化（不止改了样式）", (ct.get("persisted") or 0) >= 1,
          f"后端格子数={ct.get('persisted')}")

    print("\n【所有人都在上课时：不排除就抽不出人，并要说清原因】")
    s = d.get("allBusy") or {}
    check("此刻可抽变成 0", str(s.get("cAvail")) == "0", s.get("cAvail"))
    note = s.get("poolNote") or ""
    check("给出了原因说明", "此刻可抽 0 人" in note and ("上课" in note), note[:90])
    check("提示了下一段空闲时间或应急办法",
          ("空闲" in note) or ("忽略课表" in note), note[:90])

    print("\n【勾选「忽略课表，强制抽取」后池子恢复】")
    s = d.get("ignored") or {}
    check("此刻可抽恢复为正数", str(s.get("cAvail")) not in ("0", "–", "", "None"),
          s.get("cAvail"))
    check("说明里标注了已忽略课表", "忽略课表" in (s.get("poolNote") or ""),
          (s.get("poolNote") or "")[:80])

    print("\n【清空课表后应退化为不排除任何人】")
    check("configured 变回 False", d.get("configuredAfterClear") is False,
          d.get("configuredAfterClear"))
    s = d.get("cleared") or {}
    check("此刻可抽等于未谈人数", s.get("cAvail") == s.get("cUntalked"),
          f"可抽={s.get('cAvail')} 未谈={s.get('cUntalked')}")

    print("\n【全程不应出现初始化失败】")
    check("没有落到「无法连接后端」", not any(x.get("errored") for x in d["steps"]))

    print("\n" + "=" * 64)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
