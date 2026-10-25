"""10-15 第5步2.0 第2段：第 3 步卡片抽成共用元件（`bookclub/web/cards.js`）之後，第 3 步畫面與行為不變的快照測試。

做法：同一份第 3 步資料（`tests/snapshots/第3步卡片資料.json`，fake_workdir 的合成資料＋手加的各種卡片），
用 node 的假瀏覽器（`tests/web_harness.js`）載入第 3 步的網頁程式，對每一種卡片：
- 畫卡片（收起、展開「改做法」兩種）、畫清單、打開「改時間」面板、「＋新增修改」面板、開始前 4 件事每一頁 → 比 HTML；
- 綁了哪些按鈕事件 → 比清單；
- 每一個按鈕事件都觸發一次（確認視窗一律按確定）→ 比送出去的 API、跳出的訊息。
快照（`tests/snapshots/第3步卡片快照.json`）是抽元件**之前**的程式（commit b8814cc 的 review.js）產生的；
抽完之後的程式要產生一模一樣的東西。要刻意改第 3 步畫面時才重產：`python tests/test_cards_snapshot.py --update`。
10-15 前後 2 秒開關統一（1015-5）：宇軒授權改第 3 步卡片畫面，快照照新畫面重產過一次（播放按鈕改名、加開關、播放範圍照開關）。
10-16 #225（批次 1016-1）：宇軒授權改第 3 步畫面文字與名字卡、學員段落卡排版，快照重產（開關改名「前後多聽 2 秒」、名字卡「原句／會念成」＋收合的調整區、
聽這一段搬到標題列、學員段落卡「學員／會念成」小標、拿掉「✓ 已通過」狀態行、剪掉片段裡的卡不顯示建議框與輸入框、開始前 ②③ 說明收進問號或改列點）。
10-17 #234、#235（批次 1016-3）：宇軒授權重產，只差「開始前 名字」（③ 合成一個下拉選單；當時名冊上的人選項另加一段「名字會拿掉」的說明，
10-18 #250 已推翻、改成照原聲，選單照下一行）。
10-18 #250、#246（批次 1017-6）：宇軒授權重產，只差「開始前 名字」一行：③ 說明「書中人物、公眾人物 →『不用處理』」後面改成「（照原聲留著）」
（#246 的「卡片上寫成」、#250 的「照樣換成 某某」快照資料裡沒有這種列，畫面不變）。
1022-1：confirm/prompt/錯誤 alert 改成工具視窗（#263）。維護者同意重產；差異只在跳出視窗的文字（標題＋訊息分開、按鈕字改成動作），
送出的 API、按鈕事件、畫面 HTML 都沒變（假瀏覽器裡 uiConfirm／uiAlert／uiPrompt 記成跟以前一樣的 confirm／alert／prompt）。
1022-2：成功通知改 Toast（#264）、按鈕說明改 Tooltip（#265）。維護者同意重產；差異只在按鈕的 title= 改成 data-tip=
（說明文字不變），「開始前 4 件事」按鈕的 title 跟按鈕字一樣、直接拿掉；送出的 API、按鈕事件都沒變（#264 的成功通知這份資料沒有觸發到）。
1025-2（#292、#293）：宇軒同意（10-25 勾選）改第 3 步畫面，快照重產。改了什麼：卡片頂端一行「上一筆　第 N／M 筆　下一筆」、內部代號（T047 之類）改放滑鼠移上去的提示、
AI 建議藍框放在文字框上面、決定按鈕兩顆大的（通過、改做法）、「改時間」「這段其實是老師」等改成下面一排小字「其他：」、
清單篩選只顯示有筆數的、清單拿掉內部代號與上方那組重複入口（開始前 4 件事、全部照建議通過）與頁尾重疊說明、開始前 4 件事改成上方一排小步驟（右欄不再有總表）、
每件底部「這一件好了 → 下一件」、「全部照建議通過」降成連結。送出的 API、按鈕動作沒變（按鈕的 id 都在）。
1025-2：前後 2 秒開關放回卡片上（宇軒 10-25）：先搬到播放器旁又改回，卡片、名字卡多處列的開關位置與 10-16 的快照相同（緊接在「聽這一段」右邊，跟第 5 步一樣）。
獨立可跑：.venv/bin/python tests/test_cards_snapshot.py
"""

from __future__ import annotations

import _testtmp  # noqa: F401

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "bookclub" / "web"
SNAP = REPO_ROOT / "tests" / "snapshots"
DATA = SNAP / "第3步卡片資料.json"
GOLD = SNAP / "第3步卡片快照.json"
HARNESS = REPO_ROOT / "tests" / "web_harness.js"
NODE = shutil.which("node")
# 第 3 步頁面載入的程式（照 index.html 的順序；cards.js 是 10-15 抽出來的，舊的程式沒有）
STEP3_FILES = [p for p in ("cards.js", "review.js") if (WEB / p).is_file()]

SKELETON = ["rv-right", "rv-lower", "rv-tl", "rv-progress", "rv-legend", "rv-io", "rv-export-msg", "rv-busy",
            "rv-prep-btn", "rv-cards-btn", "rv-go4", "rv-settings", "rv-codes", "rv-clock", "rv-head"]


def _items() -> dict:
    """基本資料＋手加的卡片（每一種類型、每一種狀態都要有）。"""
    base = json.loads(DATA.read_text(encoding="utf-8"))
    items = base["項目"]
    by = {f"{x['類型']}:{x['id']}": x for x in items}
    add = []
    t = copy.deepcopy(by["學員段落:T003"])
    t.update({"id": "T009", "已確認": True, "問老師": True, "問老師備註": "這句聽不清楚", "代號改過": "Ann", "含本名": True,
              "段落外": [{"鍵": "T009", "start": 72.0, "end": 73.5, "整句": True, "答案": None},
                       {"鍵": "T009", "start": 74.0, "end": 74.6, "整句": False, "答案": "老師重念"}],
              "第5步退回": ["念錯字"], "還缺": "說話者", "標的起訖": [39.9, 71.7], "對齊": [True, False], "對齊到": "句子邊界"})
    add.append(t)
    t2 = copy.deepcopy(by["學員段落:T005"])
    t2.update({"id": "T010", "建議": {"做法": "刪除這段", "原因": "很短的一句"}, "聲紋多半是老師": {"提醒": "聲紋比較像老師"}})
    add.append(t2)
    t3 = copy.deepcopy(by["學員段落:T007"])
    t3.update({"id": "T011", "不用處理": "落在剪掉的片段裡", "剪掉的片段": "C1"})
    add.append(t3)
    add.append({"類型": "改成老師", "id": "T012", "start": 160.0, "end": 163.0, "校對稿": "老師說的第40句。", "原文": "老師說的第40句。",
                "已確認": True, "建議": {"做法": "保留老師原聲", "原因": "你改成老師了"}, "不用處理": None})
    n = copy.deepcopy(by["名字:1"])
    n.update({"id": "3", "已確認": True, "tags": ["不是名字"], "這一句的處": [{"start": 76.3, "end": 76.9}, {"start": 80.0, "end": 80.5}],
              "分開決定過": [{"start": 76.3, "做法": "整句換掉", "tags": [], "已確認": True}, {"start": 80.0, "做法": "直接消音", "tags": ["是地名"], "已確認": False}],
              "也可能是": [{"canonical": "小美美", "代號": "Mia"}], "選的人": "小美", "本名": "小美",
              "整句": {**n["整句"], "改稿": "剛剛Amy分享得很好", "範圍": "人選", "字太少": True, "字數": [8, 16], "實際會念": "剛剛Amy分享"},
              "前後沒聲音": {"說明": "重念範圍開頭有 1.2 秒沒有人講話", "可以縮": True, "建議": [76.5, 83.0], "鍵": "名字:3"},
              "同一張卡候選": ["3", "4"]})
    add.append(n)
    n2 = copy.deepcopy(by["名字:2"])
    n2.update({"id": "NM001", "老師整段": True, "人工新增": True, "可以刪": True, "做法": "整句換掉",
               "整句": {"start": 130.0, "end": 134.0, "原文": "", "換成代號": "", "改稿": "", "範圍": None}})
    add.append(n2)
    n3 = copy.deepcopy(by["名字:2"])
    n3.update({"id": "5", "整句": {**n3["整句"], "範圍": "延伸"}, "涵蓋": {"類型": "學員段落", "id": "T005", "名稱": "學員段落 01:32", "start": 92, "end": 111.6}})
    add.append(n3)
    add.append({"類型": "學員名字", "id": "SN1", "start": 150.0, "end": 150.5, "學員": "學員1", "sentence_html": "我跟<mark>阿明</mark>說",
                "整句": {"原文": "我跟阿明說", "換成代號": "我跟Tom說"}, "代號": "Tom", "信心": "低", "做法": "換成代號", "tags": [], "note": "",
                "已確認": False, "建議": {"做法": "換成代號", "原因": "保留原聲學員講到名字"}, "不用處理": None})
    o = copy.deepcopy(by["重疊:O69.60"])
    o.update({"id": "O100.00", "做法": "只留學員", "學員已選": True, "生成範圍": {"start": 99.0, "end": 103.0, "來源": "你改過的範圍",
              "老師": [{"start": 100.0, "end": 101.0, "text": "嗯對", "範圍外": [[101.0, 101.5]]}], "疊到": ["名字 2"]}, "涵蓋待通過": None})
    add.append(o)
    o2 = copy.deepcopy(by["重疊:O69.60"])
    o2.update({"id": "O110.00", "做法": "兩邊都重生成", "學員已選": False, "學員猜的": "學員2", "涵蓋待通過": None, "人工新增": True, "可以刪": True})
    add.append(o2)
    o3 = copy.deepcopy(by["重疊:O69.60"])
    o3.update({"id": "O120.00", "做法": "只留老師", "老師整句": {"start": 119.0, "end": 123.0, "原文": "老師的整句", "改稿": "改過的"}, "涵蓋待通過": None,
               "涵蓋消失": {"名稱": "學員段落 02:00", "id": "T020"}})
    add.append(o3)
    o4 = copy.deepcopy(by["重疊:O69.60"])
    o4.update({"id": "O130.00", "涵蓋": {"類型": "名字", "id": "S002", "名稱": "名字 2", "start": 119.0, "end": 124.0, "重疊項目": ["O130.00"]}, "涵蓋待通過": None})
    add.append(o4)
    add.append({"類型": "刪除段落", "id": "C1", "start": 140.0, "end": 141.0, "來源": "人工", "狀態": "刪除", "已確認": True, "備註": "",
                "建議": {"做法": "刪除", "原因": "人手動加的"}, "不用處理": None, "人工新增": True, "可以刪": True})
    add.append({"類型": "局部消音", "id": "M001", "start": 145.0, "end": 145.7, "來源": "人工", "狀態": "消音", "方式": "墊底噪", "已確認": True,
                "備註": "咳嗽", "建議": {"做法": "墊底噪", "原因": "人手動加的"}, "不用處理": None, "人工新增": True, "可以刪": True})
    add.append({"類型": "局部消音", "id": "M002", "start": 146.0, "end": 147.0, "來源": "人工", "狀態": "還原", "方式": "靜音", "已確認": True,
                "備註": "", "建議": {"做法": "靜音", "原因": "人手動加的"}, "不用處理": None})
    base["項目"] = items + add
    return base


def _scenarios(data: dict) -> list[dict]:
    keys = [f"{x['類型']}:{x['id']}" for x in data["項目"]]
    pre = (f"H.skeleton = new Set({json.dumps(SKELETON)});\n"
           f"const DATA = {json.dumps(data, ensure_ascii=False)};\n"
           "H.apiGetData['/api/review'] = DATA;\n"
           "H.api['*'] = {'段落': {'校對稿': 'x', '問老師': false}, '決定': {'做法': '整句換掉', 'tags': [], 'note': ''}, "
           "'對齊結果': {'start': 1, 'end': 2, '標的起訖': [1, 2], '對齊': [true, true], '對齊到': '句子'}, '類型': '名字', 'id': '1'};\n"
           "function setup() { rv.data = H.clone(DATA); if (typeof rvUseCardCtx === 'function') rvUseCardCtx();\n"
           "  rv.video = H.makeEl('video', {id: 'rv-video'}, ''); rv.video.currentTime = 50; rv.video.paused = true;\n"
           "  H.after = () => H.log.push(['影片', rv.video ? rv.video.currentTime : null, rv.stopAt, rv.open, rv.cur, rv.filter]); }\n")
    out = []
    for k in keys:
        for open_ in (False, True):
            out.append({"名稱": f"卡片 {k} {'展開' if open_ else '收起'}", "程式": f"""
              setup(); rv.cur = {json.dumps(k, ensure_ascii=False)}; rv.open = {str(open_).lower()};
              rvRenderCard();
              const card = document.getElementById("rv-right").innerHTML;
              const sig = H.listenerSig();
              await H.fireAll();
              return {{card, sig, log: H.log}};"""})
        out.append({"名稱": f"改時間 {k}", "程式": f"""
              setup(); const it = rvItem({json.dumps(k, ensure_ascii=False)});
              if (!RV_ITEM_KIND[it["類型"]]) return null;
              rvOpenEditor(it); const io = document.getElementById("rv-io").innerHTML; const sig = H.listenerSig();
              await H.fireAll(); return {{io, sig, log: H.log}};"""})
    for f in ("全部", "還沒確認", "學員段落", "名字", "重疊", "刪除段落", "局部消音", "改成老師", "學員名字"):
        out.append({"名稱": f"清單 {f}", "程式": f"""
              setup(); rv.cur = "名字:1"; rv.filter = {json.dumps(f, ensure_ascii=False)}; rvRenderList();
              return {{list: document.getElementById("rv-lower").innerHTML, sig: H.listenerSig()}};"""})
    for kind in ("", "刪除段落", "局部消音", "學員發言", "名字", "重疊"):
        out.append({"名稱": f"新增修改 {kind or '還沒選'}", "程式": f"""
              setup(); rvOpenEditor(null); rv.ed.kind = {json.dumps(kind or None, ensure_ascii=False)}; rv.ed.a = 10; rv.ed.b = 12; rvRenderIO();
              const io = document.getElementById("rv-io").innerHTML; const sig = H.listenerSig();
              await H.fireAll(); return {{io, sig, log: H.log}};"""})
    for tab in ("刪除", "學員", "名字", "保留原聲"):
        out.append({"名稱": f"開始前 {tab}", "程式": f"""
              setup(); rv.prepOpen = true; rv.prepTab = {json.dumps(tab, ensure_ascii=False)}; rvRenderPrepSide(); rvRenderPrep();
              return {{side: document.getElementById("rv-right").innerHTML, main: document.getElementById("rv-lower").innerHTML}};"""})
    out.append({"名稱": "整頁重畫", "程式": """
              setup(); rv.cur = "學員段落:T003"; rvRenderAll();
              return {tl: document.getElementById("rv-tl").innerHTML, prog: document.getElementById("rv-progress").innerHTML,
                      card: document.getElementById("rv-right").innerHTML, list: document.getElementById("rv-lower").innerHTML};"""})
    return [{"前置": pre, "情境": out}]


def _run(files: list[str]) -> dict:
    data = _items()
    spec = _scenarios(data)[0]
    d = Path(tempfile.mkdtemp())
    (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(HARNESS), ",".join(str(WEB / f) for f in files), str(d / "spec.json")],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_step3_cards_same_as_before():
    if not NODE:
        print("（沒有 node，略過）")
        return
    assert GOLD.is_file(), "沒有快照：先用抽元件之前的程式跑 --update"
    want = json.loads(GOLD.read_text(encoding="utf-8"))
    got = _run(STEP3_FILES)
    assert set(got) == set(want), sorted(set(got) ^ set(want))
    bad = [k for k in want if got[k] != want[k]]
    for k in bad[:3]:
        w, g = json.dumps(want[k], ensure_ascii=False), json.dumps(got[k], ensure_ascii=False)
        i = next((i for i in range(min(len(w), len(g))) if w[i] != g[i]), min(len(w), len(g)))
        print(f"✗ {k}\n  快照：…{w[max(0, i - 150):i + 150]}\n  現在：…{g[max(0, i - 150):i + 150]}")
    assert not bad, f"{len(bad)} 個情境跟抽元件之前不一樣：{bad[:10]}"
    errs = [k for k, v in got.items() if isinstance(v, dict) and "錯誤" in v and isinstance(v["錯誤"], str) and "Error" in v["錯誤"]]
    assert not errs, errs   # 情境本身跑不起來（快照裡也不能有）
    print(f"  {len(want)} 個情境一樣")


# 10-15 前後 2 秒開關統一（1015-5）：第 3 步每張卡只有一顆「前後多 2 秒」開關（標題列「聽這一段」旁、名字卡在重念範圍那一列），
# 所有播卡片範圍的按鈕（聽這一段、第 N 處、切在外面的幾秒、改做法裡的、改時間面板的）都照它：關著只播範圍本身、開著前後各多 2 秒
def test_step3_pad_switch():
    if not NODE:
        print("（沒有 node，略過）")
        return
    data = _items()
    spec = _scenarios(data)[0]
    ls = ("globalThis.LS = {store: {}, getItem(k) { return k in this.store ? this.store[k] : null; }, setItem(k, v) { this.store[k] = String(v); }};"
          " globalThis.localStorage = LS;")
    body = ls + """ if (ON) LS.store["fc-ab-pad"] = "1";
      setup(); rv.video.play = async () => {};
      const shots = {};
      async function play(key, id, sel) {
        rv.cur = key; rv.open = true; rvRenderCard();
        const l = H.listeners.filter((x) => (sel ? sel(x) : x.el.id === id) && x.type === "click").pop();
        await l.fn({target: l.el, preventDefault() {}, stopPropagation() {}});
        return [rv.video.currentTime, rv.stopAt];
      }
      const T3 = rvItem("學員段落:T009"), N3 = rvItem("名字:3"), O = rvItem("重疊:O100.00");
      shots.only = [await play("學員段落:T009", "rv-playonly"), T3.start, T3.end];
      shots.out = [await play("學員段落:T009", null, (x) => x.el.className.includes("rv-outplay")), T3["段落外"][1].start, T3["段落外"][1].end];
      shots.range = [await play("名字:3", "rv-playonly"), N3["整句"].start, N3["整句"].end];   // 10-16 #225：名字卡的「聽這一段」搬到標題列，播重念範圍
      shots.spot = [await play("名字:3", null, (x) => x.el.className.includes("rv-segplay")), N3["這一句的處"][1].start, N3["這一句的處"][1].end];
      shots.gen = [await play("重疊:O100.00", "rv-genplay"), O["生成範圍"].start, O["生成範圍"].end];
      const cards = {};
      for (const k of ["學員段落:T009", "名字:3", "重疊:O100.00", "刪除段落:C1"]) { rv.cur = k; rv.open = true; rvRenderCard(); cards[k] = document.getElementById("rv-right").innerHTML; }
      // 10-16 #225：名字:3 是「不是名字」，不會重念、沒有調整區；拿掉標記看要重念時的調整區
      const tg = N3.tags; N3.tags = []; rv.cur = "名字:3"; rvRenderCard(); cards["名字:3 要重念"] = document.getElementById("rv-right").innerHTML; N3.tags = tg;
      rvOpenEditor(T3); H.listeners.length = 0; rvRenderIO();
      const te = H.listeners.filter((x) => "tePlay" in x.el.dataset && x.type === "click").pop();
      await te.fn({}); shots.te = [[rv.video.currentTime, rv.stopAt], rv.ed.a, rv.ed.b];
      rv.cur = "名字:3"; rvRenderCard();
      const pad = document.getElementById("rv-pad"); pad.checked = !ON;
      await H.listeners.filter((x) => x.el.id === "rv-pad" && x.type === "change").pop().fn({});
      return {shots, cards, stored: LS.store["fc-ab-pad"], padOn: cvPad()};"""
    spec["情境"] = [{"名稱": n, "程式": f"const ON = {o};" + body} for n, o in (("關", "false"), ("開", "true"))]
    d = Path(tempfile.mkdtemp())
    (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([NODE, str(HARNESS), ",".join(str(WEB / f) for f in STEP3_FILES), str(d / "spec.json")],
                       capture_output=True, text=True, check=True)
    got = json.loads(r.stdout)
    for name, k in (("關", 0), ("開", 2)):
        v = got[name]
        assert "錯誤" not in v, v
        for what, (seen, a, b) in v["shots"].items():
            assert [round(x, 3) for x in seen] == [round(max(0, a - k), 3), round(b + k, 3)], (name, what, seen, a, b)
        for key, html in v["cards"].items():
            assert html.count("data-cv-pad") == 1, (name, key)   # 一張卡一顆
            assert ("checked" in html.split('id="rv-pad"')[1].split(">")[0]) == (k == 2), (name, key)
            for old in ("只播這一段", "只播重念範圍", "播這一段", "試聽這幾秒", "試聽這段"):
                assert old not in html.replace("聽這一段", ""), (name, key, old)
        assert 'rv-namefold' not in v["cards"]["名字:3"] and 'rv-edge' not in v["cards"]["名字:3"]   # 不是名字：沒有調整區、不提醒縮範圍
        nm = v["cards"]["名字:3 要重念"]
        assert 'class="rv-namefold"' in nm and 'class="linkish rv-reset" id="rv-rangereset"' in nm
        assert nm.index('id="rv-rangereset"') > nm.index('data-rg="1"')   # 回到預設範圍在起訖欄後面
    assert got["關"]["stored"] == "1" and got["關"]["padOn"] == 2   # 撥開關：記在第 5 步同一個鍵，馬上生效
    assert got["開"]["stored"] == "0" and got["開"]["padOn"] == 0


if __name__ == "__main__":
    if "--update" in sys.argv:
        GOLD.write_text(json.dumps(_run(STEP3_FILES), ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        print(f"已重產快照：{GOLD}（用 {', '.join(STEP3_FILES)}）")
        sys.exit(0)
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"✓ {fn.__name__}")
    print(f"{len(tests)}/{len(tests)} 通過")
