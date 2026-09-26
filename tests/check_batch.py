"""名单批量操作（勾选 → 停用/启用/删除）的界面测试。

为什么必须实测：这里是「勾选状态 + 表格重绘 + 模态确认 + 一个已存在的 change 委托监听器」
四者交汇的地方，任何一处对不上都会表现成"点了没反应"或"弹出莫名其妙的错误"。
（写这个功能时就发现：行内编辑的监听器会把勾选框当成编辑事件处理。）

运行：python tests/check_batch.py
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
  function T(id) { var e = document.getElementById(id); return e ? e.textContent.trim() : ""; }
  function rows() { return document.querySelectorAll("#roster-body tr").length; }
  function chks() { return document.querySelectorAll("#roster-body .row-chk").length; }
  function picked() { return document.querySelectorAll("#roster-body .row-chk:checked").length; }
  function vis(el) {   // 元素是否真在视口内可见（DOM 里存在不等于看得见）
    if (!el) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight + 1;
  }
  function hit(el) {   // 中心点是否真能被点到（没被别的层盖住）
    if (!el) return "null";
    var r = el.getBoundingClientRect();
    var h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!h) return "null";
    return (h === el || el.contains(h)) ? "self" : h.tagName + (h.id ? "#" + h.id : "");
  }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  try {
    await wait(600);
    await call("import_text", "姓名,学号,班级\n甲,1,一班\n乙,2,一班\n丙,3,一班\n丁,4,一班\n戊,5,一班\n");
    await refresh();

    document.querySelectorAll(".tabs button")[1].click();   // 名单管理
    await wait(500);
    out.rowCount = rows();
    out.chkCount = chks();
    out.barHiddenAtStart = document.getElementById("sel-bar").hidden;

    // 1) 全选
    var all = document.getElementById("chk-all");
    all.checked = true;
    all.dispatchEvent(new Event("change"));
    await wait(400);
    out.afterSelectAll = {
      picked: picked(), count: T("sel-count"),
      barHidden: document.getElementById("sel-bar").hidden,
      barVisible: vis(document.getElementById("sel-bar")),
      deleteHit: hit(document.getElementById("btn-sel-delete")),
      disableHit: hit(document.getElementById("btn-sel-disable")),
    };

    // 2) 批量停用
    document.getElementById("btn-sel-disable").onclick();
    await wait(900);
    var st = await call("state");
    out.afterDisable = {
      disabledInDb: st.stats.disabled,
      remainingInDb: st.stats.remaining,
      untalkedShown: T("c-untalked"),
    };

    // 3) 批量启用回来
    document.getElementById("btn-sel-enable").onclick();
    await wait(900);
    var st2 = await call("state");
    out.afterEnable = { disabledInDb: st2.stats.disabled, remainingInDb: st2.stats.remaining };

    // 4) 取消选择
    document.getElementById("btn-sel-clear").onclick();
    await wait(400);
    out.afterClear = { picked: picked(), barHidden: document.getElementById("sel-bar").hidden };

    // 5) 只勾两个，批量删除（要过模态确认）
    var boxes = document.querySelectorAll("#roster-body .row-chk");
    for (var i = 0; i < 2; i++) {
      boxes[i].checked = true;
      boxes[i].dispatchEvent(new Event("change", { bubbles: true }));
    }
    await wait(300);
    out.beforeDelete = { picked: picked(), count: T("sel-count") };
    document.getElementById("btn-sel-delete").onclick();
    await wait(500);
    out.modalShown = !document.getElementById("modal").hidden;
    out.modalTitle = T("modal-title");
    document.getElementById("modal-ok").click();     // 确认删除
    await wait(1200);
    var st3 = await call("state");
    out.afterDelete = {
      studentsInDb: st3.stats.total,
      rowsShown: rows(),
      pickedAfter: picked(),
      barHidden: document.getElementById("sel-bar").hidden,
      emptyNote: document.querySelectorAll("#history-list .empty-note").length,
    };

    // 6) 筛选后全选，只应作用于筛选出的行
    var inp = document.getElementById("roster-search");
    inp.value = "丙";
    inp.dispatchEvent(new Event("input"));
    await wait(400);
    var all2 = document.getElementById("chk-all");
    all2.checked = true;
    all2.dispatchEvent(new Event("change"));
    await wait(400);
    out.filteredSelect = { rows: rows(), picked: picked(), count: T("sel-count") };
    inp.value = "";
    inp.dispatchEvent(new Event("input"));
    await wait(300);

    out.errors = window.__ERRORS__ || [];
  } catch (e) {
    out.error = String((e && e.message) || e);
  }
  var pre = document.createElement("pre");
  pre.id = "BATCHOUT";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script>
"""


def main() -> int:
    edge = find_edge()
    if not edge:
        print("未找到 Microsoft Edge，跳过批量操作测试")
        return 0

    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append(name)
        if not ok:
            failures.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + str(detail)) if detail else ''}")

    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "batch.html"
        gen_html.build(ROOT / "students.csv", page)
        text = page.read_text(encoding="utf-8")
        # 记录页面 JS 报错，行内编辑监听器冲突就是靠这个抓的
        text = text.replace("<head>", "<head><script>window.__ERRORS__=[];"
                                      "window.addEventListener('error',function(e){"
                                      "window.__ERRORS__.push(String(e.message));});</script>")
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

    m = re.search(r'id="BATCHOUT">(.*?)</pre>', raw, re.S)
    if not m:
        print("探针没有输出，页面可能没有正常加载")
        return 1
    d = json.loads(html_mod.unescape(m.group(1)))
    if d.get("error"):
        print("探针内抛错：", d["error"])
        return 1

    print("=" * 64)
    print("【勾选列与操作条】")
    check("每行都有勾选框", d.get("chkCount") == d.get("rowCount"),
          f"勾选框 {d.get('chkCount')} / 行 {d.get('rowCount')}")
    check("初始不显示操作条", d.get("barHiddenAtStart") is True)

    print("\n【全选】")
    s = d.get("afterSelectAll") or {}
    check("全选后勾中全部行", s.get("picked") == d.get("rowCount"), s)
    check("操作条出现并显示人数", s.get("barHidden") is False and
          str(s.get("count")) == str(d.get("rowCount")), s)
    check("操作条真的在视口内可见", s.get("barVisible") is True, s)
    check("「删除」按钮可被点中", s.get("deleteHit") == "self", s.get("deleteHit"))
    check("「停用」按钮可被点中", s.get("disableHit") == "self", s.get("disableHit"))

    print("\n【批量停用 / 启用】")
    s = d.get("afterDisable") or {}
    check("库里全部变成已停用", s.get("disabledInDb") == d.get("rowCount"), s)
    check("未谈人数归零", str(s.get("untalkedShown")) == "0" or s.get("remainingInDb") == 0, s)
    s = d.get("afterEnable") or {}
    check("批量启用后恢复", s.get("disabledInDb") == 0 and
          s.get("remainingInDb") == d.get("rowCount"), s)

    print("\n【取消选择】")
    s = d.get("afterClear") or {}
    check("取消后无勾选且操作条隐藏",
          s.get("picked") == 0 and s.get("barHidden") is True, s)

    print("\n【批量删除（含确认）】")
    s = d.get("beforeDelete") or {}
    check("只勾两行时计数为 2", str(s.get("count")) == "2", s)
    check("弹出了确认框", d.get("modalShown") is True, d.get("modalTitle"))
    s = d.get("afterDelete") or {}
    check("库里少了两名学生",
          s.get("studentsInDb") == (d.get("rowCount") or 0) - 2, s)
    check("表格同步刷新", s.get("rowsShown") == s.get("studentsInDb"), s)
    check("删除后自动清空勾选", s.get("pickedAfter") == 0 and s.get("barHidden") is True, s)

    print("\n【筛选后全选只作用于筛选出的行】")
    s = d.get("filteredSelect") or {}
    check("搜索「丙」后只剩 1 行", s.get("rows") == 1, s.get("rows"))
    check("全选只勾中这 1 行", s.get("picked") == 1 and str(s.get("count")) == "1", s)

    print("\n【不该有 JS 报错（勾选框曾误触发行内编辑）】")
    check("页面无 JS 异常", not (d.get("errors") or []), d.get("errors"))

    print("\n" + "=" * 64)
    print(f"共 {len(checks)} 项，失败 {len(failures)} 项")
    for f in failures:
        print("  × " + f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
