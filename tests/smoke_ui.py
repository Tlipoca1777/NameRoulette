"""界面集成测试：驱动真实 pywebview 窗口，验证渲染结果与数据库一致。

不用 evaluate_js（pywebview 6.x 在 Windows 上会因 window.native 序列化
无限递归而卡住），改为在页面副本里注入探针脚本，由页面主动回传 DOM 内容。
产品代码 web/index.html 不做任何修改。

运行：python tests/smoke_ui.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod  # noqa: E402
import core  # noqa: E402
import db  # noqa: E402
import webview  # noqa: E402

SEED = 20
PROBE_TIMEOUT = 40          # 秒，超时后强制关窗
reports = []
page_errors = []
failures = []
checks = []


def check(name, ok, detail=""):
    checks.append(name)
    if not ok:
        failures.append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}",
          flush=True)


PROBE_JS = """
<script>
(function () {
  window.__ERRORS__ = [];
  window.addEventListener("error", function (e) {
    window.__ERRORS__.push(String(e.message) + " @ " + e.filename + ":" + e.lineno);
  });
  var _err = console.error;
  console.error = function () {
    window.__ERRORS__.push("console.error: " + Array.prototype.join.call(arguments, " "));
    _err.apply(console, arguments);
  };

  // 取元素的文本；元素不存在时返回空字符串。
  // 不能返回 undefined——JSON.stringify 会把它整个键丢掉，
  // 于是 Python 端取键就 KeyError，排查起来很绕。
  function sv(el) { return el && el.textContent ? el.textContent : ""; }

  function hitOk(el) {
    var r = el.getBoundingClientRect();
    var hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit) return "null";
    return (hit === el || el.contains(hit)) ? "self"
      : hit.tagName + (hit.id ? "#" + hit.id : "");
  }
  function snap(tag) {
    var api = window.pywebview && window.pywebview.api;
    if (!api || !api.ui_probe) return;
    var vh = window.innerHeight;
    var draw = document.getElementById("view-draw");
    var roster = document.getElementById("view-roster");
    var imp = document.getElementById("btn-import-file");
    var ir = imp.getBoundingClientRect();
    api.ui_probe(JSON.stringify({
      tag: tag,
      total: (document.getElementById("c-total") || {}).textContent,
      remaining: (document.getElementById("c-available") || {}).textContent,
      round: (document.getElementById("c-round") || {}).textContent,
      name: (document.getElementById("display-name") || {}).textContent,
      meta: (document.getElementById("display-meta") || {}).textContent,
      hist: document.querySelectorAll("#history-list .hitem").length,
      hgroups: document.querySelectorAll("#history-list .hgroup").length,
      groupLabel: sv(document.querySelector("#history-list .hgroup span")),
      histHint: sv(document.getElementById("history-hint")),
      emptyNote: document.querySelectorAll("#history-list .empty-note").length,
      rosterRows: document.querySelectorAll("#roster-body tr").length,
      rosterCount: (document.getElementById("roster-count") || {}).textContent,
      viewportH: vh,
      drawDisplay: getComputedStyle(draw).display,
      rosterDisplay: getComputedStyle(roster).display,
      importBtnVisible: ir.width > 0 && ir.height > 0 && ir.top >= 0 && ir.bottom <= vh + 1,
      importBtnHit: hitOk(imp),
      errors: window.__ERRORS__.slice(-5)
    }));
  }

  var steps = [
    [1200, function () { snap("初始化后"); }],
    [1600, function () { document.getElementById("btn-draw").click(); }],
    [5200, function () { snap("抽取后"); }],
    [5600, function () { document.getElementById("btn-draw").click(); }],
    [9200, function () { snap("再抽一次后"); }],
    [9600, function () {
      var b = document.querySelector("#history-list [data-undo]");
      if (b) b.click();
    }],
    [12600, function () { snap("撤回后"); }],
    [13000, function () {
      document.querySelector('.tabs button[data-view="roster"]').click();
    }],
    [14200, function () { snap("名单管理页"); }],
    [14800, function () {
      var el = document.getElementById("roster-search");
      el.value = "zzz不存在zzz";
      el.dispatchEvent(new Event("input"));
    }],
    [15800, function () { snap("搜索无结果"); }],
    [16400, function () {
      var api = window.pywebview.api;
      api.ui_probe(JSON.stringify({ tag: "__DONE__", errors: window.__ERRORS__ }));
    }]
  ];

  function arm(i) {
    if (i >= steps.length) return;
    setTimeout(function () {
      try { steps[i][1](); } catch (e) { window.__ERRORS__.push("step: " + e); }
      arm(i + 1);
    }, i === 0 ? steps[0][0] : steps[i][0] - steps[i - 1][0]);
  }

  function boot() { arm(0); }
  if (window.pywebview) { boot(); }
  else { window.addEventListener("pywebviewready", boot); }
})();
</script>
"""


class ProbeApi(appmod.Api):
    """真实 Api 加上探针接收口。

    关键：数据库快照必须和界面快照在同一时刻采集，
    否则断言时读到的是流程结束后的最终状态，无法反映各步骤的真实情况。
    """

    def ui_probe(self, payload):
        try:
            data = json.loads(payload)
        except Exception:
            data = {"tag": "parse-error", "raw": str(payload)[:200]}
        if data.get("tag") == "__DONE__":
            page_errors.extend(data.get("errors") or [])
            self._window.destroy()
            return {"ok": True}
        with self._lock:
            rnd = core.get_round(self._conn)
            data["dbDraws"] = [dict(r) for r in core.history(self._conn, round=rnd)]
            data["dbPool"] = len(core.pool_names(self._conn))
        reports.append(data)
        return {"ok": True}


def build_probe_page(tmpdir) -> str:
    src = appmod.resource_path("web/index.html").read_text(encoding="utf-8")
    assert "</body>" in src
    out = src.replace("</body>", PROBE_JS + "\n</body>")
    path = os.path.join(tmpdir, "probe.html")
    Path(path).write_text(out, encoding="utf-8")
    return path


def run_checks(window, conn, tmpdir):
    time.sleep(PROBE_TIMEOUT)


def by_tag(tag):
    for r in reports:
        if r.get("tag") == tag:
            return r
    return None


def main():
    tmpdir = tempfile.mkdtemp(prefix="rollcall_ui_")
    conn = db.connect(os.path.join(tmpdir, "test.db"))
    for i in range(1, SEED + 1):
        core.add_student(conn, f"测试学生{i:02d}", f"2024{i:04d}", "测试班")

    page = build_probe_page(tmpdir)

    api = ProbeApi(conn=conn)
    window = webview.create_window("界面集成测试", url=page, js_api=api,
                                   width=1200, height=780)
    api._window = window
    webview.start(run_checks, (window, conn, tmpdir))

    print("=" * 64, flush=True)
    if not reports:
        print("失败：页面没有任何回传 —— 前后端桥接未打通", flush=True)
        sys.exit(1)

    init = by_tag("初始化后")
    after1 = by_tag("抽取后")
    after2 = by_tag("再抽一次后")
    undid = by_tag("撤回后")
    roster = by_tag("名单管理页")
    searched = by_tag("搜索无结果")

    print("\n【1】初始化后界面数据应与数据库一致", flush=True)
    check("收到初始化快照", init is not None)
    if init:
        check("总人数显示正确", str(init["total"]) == str(SEED),
              f"界面={init['total']} 期望={SEED}")
        check("此刻可抽人数显示正确", str(init["remaining"]) == str(SEED),
              f"界面={init['remaining']}")
        check("轮次显示正确", str(init["round"]) == "1", f"界面={init['round']}")
        check("无记录时显示空状态提示", init["emptyNote"] == 1)

    print("\n【2】点击「开始抽取」后应显示姓名、人数递减、写入历史", flush=True)
    check("收到抽取后快照", after1 is not None)
    if after1:
        d1 = after1["dbDraws"]
        check("数据库已写入 1 条抽取记录", len(d1) == 1, f"记录数={len(d1)}")
        check("大屏显示出被抽中的姓名",
              after1["name"] not in ("准备就绪", "–", "", None),
              f"显示={after1['name']!r}")
        if d1:
            check("界面姓名与数据库记录一致", after1["name"] == d1[0]["name"],
                  f"界面={after1['name']!r} 数据库={d1[0]['name']!r}")
        check("此刻可抽递减到 19", str(after1["remaining"]) == str(SEED - 1),
              f"界面={after1['remaining']}")
        check("历史列表显示 1 条", after1["hist"] == 1, f"={after1['hist']}")
        check("历史按日期分组，标题含「今天」",
              after1.get("hgroups") == 1 and "今天" in (after1.get("groupLabel") or ""),
              f"组数={after1.get('hgroups')} 标题={after1.get('groupLabel')!r}")
        check("提示显示今天已抽 1 人",
              "今天已抽 1 人" in (after1.get("histHint") or ""), after1.get("histHint"))
        check("大屏副标题带日期",
              "月" in (after1["meta"] or "") and "周" in (after1["meta"] or ""),
              after1["meta"])
        check("副标题显示学号/班级", bool((after1["meta"] or "").strip()),
              f"={after1['meta']!r}")
        check("被抽中者已移出抽取池", after1["dbPool"] == SEED - 1,
              f"池中 {after1['dbPool']} 人")

    print("\n【3】再抽一次，累计 2 条且互不重复", flush=True)
    if after2:
        d2 = after2["dbDraws"]
        check("累计 2 条记录", len(d2) == 2, f"记录数={len(d2)}")
        if len(d2) == 2:
            names = [d["name"] for d in d2]
            check("两次抽到不同的人", names[0] != names[1], f"={names}")
            check("界面显示第二个被抽中者", after2["name"] == names[1],
                  f"界面={after2['name']!r} 数据库={names[1]!r}")
        check("此刻可抽递减到 18", str(after2["remaining"]) == str(SEED - 2),
              f"界面={after2['remaining']}")

    print("\n【4】点击「撤回」应把学生放回抽取池", flush=True)
    if undid:
        d3 = undid["dbDraws"]
        check("撤回后数据库记录回到 1 条", len(d3) == 1, f"记录数={len(d3)}")
        check("撤回的学生已回到抽取池", undid["dbPool"] == SEED - 1,
              f"池中 {undid['dbPool']} 人，期望 {SEED - 1}")
        check("界面此刻可抽同步为 19", str(undid["remaining"]) == str(SEED - 1),
              f"界面={undid['remaining']}")
        check("历史列表减为 1 条", undid["hist"] == 1, f"={undid['hist']}")

    print("\n【5】名单管理页应渲染出全部学生，并且真的能看到、能点到", flush=True)
    check("收到名单页快照", roster is not None)
    if roster:
        check("表格行数等于学生数", roster["rosterRows"] == SEED,
              f"行数={roster['rosterRows']} 期望={SEED}")
        check("显示名单人数文案", str(SEED) in str(roster["rosterCount"]),
              f"={roster['rosterCount']!r}")
        # 下面这几项是修 CSS 优先级 bug 时补的：只数元素个数查不出「页面被挤出视口」
        check("抽取页已真正隐藏", roster["drawDisplay"] == "none",
              f"display={roster['drawDisplay']}")
        check("名单页已显示", roster["rosterDisplay"] != "none",
              f"display={roster['rosterDisplay']}")
        check("导入按钮在视口内", roster["importBtnVisible"],
              f"视口高={roster['viewportH']}")
        check("导入按钮可被点中", roster["importBtnHit"] == "self",
              f"命中={roster['importBtnHit']}")

    print("\n【6】搜索框应能筛选", flush=True)
    if searched:
        check("搜索无结果时表格为空", searched["rosterRows"] == 0,
              f"行数={searched['rosterRows']}")

    print("\n【7】页面不应有 JavaScript 报错", flush=True)
    all_errors = page_errors or (init or {}).get("errors") or []
    check("无 JS 异常", len(all_errors) == 0,
          f"错误：{all_errors[:3]}" if all_errors else "")

    print("\n" + "=" * 64, flush=True)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项", flush=True)
    for f in failures:
        print("  × " + f, flush=True)
    print("\n共收到 %d 个快照: %s" % (len(reports), [r.get("tag") for r in reports]),
          flush=True)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
