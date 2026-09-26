"""抽取历史「按日期分组」与日期显示测试。

在真实浏览器里构造跨天的抽取记录，检查分组标题、顺序、每组条数和
「今天已抽 N 人」提示。这类日期逻辑必须测跨天，只测当天是测不出来的。

运行：python tests/check_history.py
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
  try {
    importText("姓名,学号,班级\n甲同学,1,一班\n乙同学,2,一班\n丙同学,3,一班\n丁同学,4,一班\n");

    function day(offset) {
      var x = new Date();
      x.setDate(x.getDate() - offset);
      return x.getFullYear() + "-" + pad2(x.getMonth() + 1) + "-" + pad2(x.getDate());
    }
    out.days = { today: day(0), yesterday: day(1), threeAgo: day(3) };

    // 三天前 1 条、昨天 1 条、今天 2 条，故意乱序写入以验证渲染时排序
    DB.draws = [
      { id: 11, studentId: 1, seq: 1, round: 1, drawnAt: day(3) + " 08:05:00",
        name: "丙同学", sid: "3", cls: "一班" },
      { id: 12, studentId: 2, seq: 2, round: 1, drawnAt: day(1) + " 09:10:00",
        name: "丁同学", sid: "4", cls: "一班" },
      { id: 13, studentId: 3, seq: 3, round: 1, drawnAt: day(0) + " 07:30:00",
        name: "甲同学", sid: "1", cls: "一班" },
      { id: 14, studentId: 4, seq: 4, round: 1, drawnAt: day(0) + " 09:45:00",
        name: "乙同学", sid: "2", cls: "一班" }
    ];
    DB.nextDrawId = 20;
    DB.round = 1;
    save();
    await refresh();

    out.groups = [].map.call(
      document.querySelectorAll("#history-list .hgroup"),
      function (g) {
        return {
          label: g.querySelector("span").textContent.trim(),
          count: g.querySelector(".hcount").textContent.trim(),
          past: g.classList.contains("past")
        };
      }
    );
    out.names = [].map.call(
      document.querySelectorAll("#history-list .hwho"),
      function (n) { return n.textContent.trim(); }
    );
    out.times = [].map.call(
      document.querySelectorAll("#history-list .htime"),
      function (n) { return n.textContent.trim(); }
    );
    out.subs = [].map.call(
      document.querySelectorAll("#history-list .hsub"),
      function (n) { return n.textContent.trim(); }
    );
    out.items = document.querySelectorAll("#history-list .hitem").length;
    out.hint = document.getElementById("history-hint").textContent.trim();
    out.newestCount = document.querySelectorAll("#history-list .hitem.newest").length;
    out.stageMeta = document.getElementById("display-meta").textContent.trim();

    // 撤回后分组应减少对应条目
    undo(14);
    await refresh();
    out.afterUndo = {
      items: document.querySelectorAll("#history-list .hitem").length,
      hint: document.getElementById("history-hint").textContent.trim(),
      groups: document.querySelectorAll("#history-list .hgroup").length
    };

    // 模拟「开始新学期」：学期加一，但以前的日期和记录必须还在
    DB.round = 2;
    DB.students.forEach(function (s) { s.drawn = false; s.drawnAt = null; });
    save();
    await refresh();
    out.afterReset = {
      items: document.querySelectorAll("#history-list .hitem").length,
      groups: document.querySelectorAll("#history-list .hgroup").length,
      labels: [].map.call(document.querySelectorAll("#history-list .hgroup span:first-child"),
                          function (n) { return n.textContent.trim(); }),
      badges: [].map.call(document.querySelectorAll("#history-list .hround"),
                          function (n) { return n.textContent.trim(); }),
      times: [].map.call(document.querySelectorAll("#history-list .htime"),
                         function (n) { return n.textContent.trim(); })
    };
  } catch (e) {
    out.error = String((e && e.message) || e);
  }
  var pre = document.createElement("pre");
  pre.id = "HISTOUT";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script>
"""


def main() -> int:
    edge = find_edge()
    if not edge:
        print("未找到 Microsoft Edge，跳过历史分组测试")
        return 0

    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "hist.html"
        gen_html.build(ROOT / "students.csv", page)
        text = page.read_text(encoding="utf-8")
        page.write_text(text.replace("</body>", PROBE + "</body>"), encoding="utf-8")

        dump = Path(td) / "dump.html"
        with dump.open("w", encoding="utf-8") as f:
            subprocess.run(
                [edge, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--virtual-time-budget=12000", "--window-size=1280,830",
                 "--dump-dom", page.as_uri()],
                stdout=f, stderr=subprocess.DEVNULL, timeout=180,
            )
        raw = dump.read_text(encoding="utf-8", errors="replace")

    m = re.search(r'id="HISTOUT">(.*?)</pre>', raw, re.S)
    if not m:
        print("探针没有输出，页面可能没有正常加载")
        return 1
    d = json.loads(html_mod.unescape(m.group(1)))
    if d.get("error"):
        print("探针内抛错：", d["error"])
        return 1

    print("=" * 64)
    groups = d.get("groups", [])
    print("【分组结果】")
    for g in groups:
        print(f"    {g['label']}  ({g['count']}, past={g['past']})")

    print("\n【按日期分组】")
    check("分成 3 天 3 组", len(groups) == 3, f"实际 {len(groups)} 组")
    if len(groups) == 3:
        check("最上面是今天", groups[0]["label"].startswith("今天"), groups[0]["label"])
        check("其次是昨天", groups[1]["label"].startswith("昨天"), groups[1]["label"])
        check("更早的显示具体日期",
              "今天" not in groups[2]["label"] and "昨天" not in groups[2]["label"]
              and "月" in groups[2]["label"], groups[2]["label"])
        check("今天组的条数为 2", groups[0]["count"] == "2 人", groups[0]["count"])
        check("今天组高亮、往期组不高亮",
              not groups[0]["past"] and groups[1]["past"] and groups[2]["past"],
              [g["past"] for g in groups])

    print("\n【排序与内容】")
    check("共 4 条记录", d.get("items") == 4, d.get("items"))
    check("最近的日期排在最前、同日按时间正序",
          d.get("names") == ["甲同学", "乙同学", "丁同学", "丙同学"], d.get("names"))
    check("每条显示时间（HH:MM）",
          all(re.fullmatch(r"\d{2}:\d{2}", t) for t in d.get("times", [])),
          d.get("times"))
    check("副行只留学号与班级、不再挤时间",
          all(":" not in t for t in d.get("subs", [])), d.get("subs"))
    check("最近一次抽取被高亮", d.get("newestCount") == 1, d.get("newestCount"))

    print("\n【今天已抽提示】")
    check("提示显示今天已抽 2 人", "今天已抽 2 人" in d.get("hint", ""), d.get("hint"))

    print("\n【大屏空闲时显示今天日期】")
    check("大屏副标题含「今天」和「月」",
          "今天" in d.get("stageMeta", "") and "月" in d.get("stageMeta", ""),
          d.get("stageMeta"))

    print("\n【撤回后分组同步更新】")
    au = d.get("afterUndo", {})
    check("撤回后剩 3 条", au.get("items") == 3, au.get("items"))
    check("撤回后提示变为今天已抽 1 人",
          "今天已抽 1 人" in au.get("hint", ""), au.get("hint"))
    check("撤回后仍为 3 组（前天的记录不消失）", au.get("groups") == 3, au.get("groups"))

    print("\n【开始新学期后，以前的日期仍然可见】")
    ar = d.get("afterReset", {})
    check("新学期后旧记录条数不变", ar.get("items") == 3, ar.get("items"))
    check("新学期后仍是 3 个日期分组", ar.get("groups") == 3, ar.get("groups"))
    labels = ar.get("labels") or []
    check("新学期后仍显示今天/昨天/具体日期",
          len(labels) == 3 and labels[0].startswith("今天")
          and labels[1].startswith("昨天"), labels)
    check("旧记录带「第1学期」标记以区分",
          ar.get("badges") == ["第1学期", "第1学期", "第1学期"], ar.get("badges"))
    check("旧记录的时间仍然保留",
          all(re.fullmatch(r"\d{2}:\d{2}", t) for t in (ar.get("times") or [])),
          ar.get("times"))

    print("\n" + "=" * 64)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
