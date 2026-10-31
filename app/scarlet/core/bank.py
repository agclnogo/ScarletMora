"""索引（bank）を作る。モーラ → どこに有るか。"""

from __future__ import annotations

import collections
import csv
import glob
import os

from .. import paths
from . import kana as K
from .store import read_marks


def load_all(out_dir: str) -> dict[str, list]:
    data = {}
    for p in sorted(glob.glob(os.path.join(out_dir, "marks", "*.csv"))):
        name = os.path.splitext(os.path.basename(p))[0]
        data[name] = read_marks(p)
    return data


def build_bank(out_dir: str, src_dir: str) -> dict:
    data = load_all(out_dir)
    rows = []
    for name, marks in data.items():
        for m in marks:
            rows.append({
                "mora": m.mora,
                "source": name,
                "start": round(m.start, 3),
                "end": round(m.end, 3),
                "dur": round(m.dur, 3),
                "conf": round(m.conf, 3),
                "peak_db": round(m.peak_db, 1),
                "dip": round(m.dip, 1),
                "pos": "句头" if m.idx == 0 or (marks[m.idx - 1].phrase != m.phrase) else "句中",
                "flags": m.flags,
            })

    path = os.path.join(out_dir, "bank.csv")
    cols = ["mora", "source", "start", "end", "dur", "conf", "peak_db", "dip", "pos", "flags"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    cnt = collections.Counter(r["mora"] for r in rows)
    clean = collections.Counter(r["mora"] for r in rows if not r["flags"])
    lines = [f"ユニークなモーラ: {len(cnt)} / 総モーラ: {len(rows)}", ""]
    lines.append(f"{'モーラ':<6}{'総数':>6}{'無印':>6}")
    for k, v in sorted(cnt.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"{k:<6}{v:>6}{clean.get(k, 0):>6}")
    with open(os.path.join(out_dir, "bank_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ブラウザで見たいとき用の索引ページ（アプリ内の「音库索引」と同じ中身）
    try:
        _write_index_html(out_dir, rows, cnt, clean)
    except Exception:
        pass
    return {"rows": rows, "count": cnt}


def write_report(out_dir: str) -> str:
    """report.txt: 曲目ごとの 音数 / 可信率 / 均信 / 标记数。

    並びは 可信率の低い順（直しがいのある曲が上に来る）。
    可信率 = conf > 0.4 のモーラの割合（%・四捨五入）。
    同じ可信率のときは音库に取り込んだ順（motools 版と同じ並び）。
    """
    data = load_all(out_dir)
    order = _import_order(out_dir)
    rows: list[tuple[float, int, str, int, int, float, int]] = []
    for name, marks in data.items():
        n = len(marks)
        if not n:
            continue
        hit = sum(1 for m in marks if m.conf > 0.4)
        rate = round(100 * hit / n)
        avg = sum(m.conf for m in marks) / n
        flagged = sum(1 for m in marks if (m.flags or "").strip())
        rows.append((hit / n, order.get(name, 10 ** 9), name, n, rate, avg, flagged))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    lines = [f"{'文件':<10}{'音数':>7}{'可信率':>7}{'均信':>7}{'标记数':>7}"]
    for _frac, _ord, name, n, rate, avg, flagged in rows:
        lines.append(f"{name:<10}{n:>7}{f'{rate}%':>7}{f'{avg:.2f}':>7}{flagged:>7}")

    path = os.path.join(out_dir, "report.txt")
    # motools 版と同じ: BOM 無し・CRLF・末尾に改行を足さない
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines))
    return path


def _import_order(out_dir: str) -> dict[str, int]:
    """library.json の曲目順（取り込んだ順）。無ければ空。"""
    import json

    try:
        with open(os.path.join(out_dir, "library.json"), encoding="utf-8") as f:
            d = json.load(f) or {}
        return {t.get("name"): i for i, t in enumerate(d.get("tracks") or [])
                if t.get("name")}
    except Exception:
        return {}


def _write_index_html(out_dir: str, rows: list[dict], cnt=None, clean=None) -> None:
    """音库索引のオフライン閲覧用ページ。clips/<mora>.wav をその場で試聴できる。"""
    import json

    cnt = cnt or collections.Counter(r["mora"] for r in rows)
    clean = clean or collections.Counter(r["mora"] for r in rows if not r["flags"])
    moras = [{"mora": k, "label": K.mora_label(k), "n": v, "clean": clean.get(k, 0)}
             for k, v in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))]
    payload = json.dumps({"moras": moras, "rows": rows}, ensure_ascii=False)
    html = _INDEX_TPL.replace("/*__DATA__*/null", payload)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


_INDEX_TPL = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>音库索引</title>
<style>
 :root{--bg:#12141a;--bg1:#191c24;--bg2:#232734;--fg:#e7e9ee;--dim:#8b93a7;
       --acc:#ff3355;--line:#2e3342}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.5 "Microsoft YaHei UI",system-ui,sans-serif}
 header{display:flex;align-items:baseline;gap:14px;padding:14px 20px;
        border-bottom:1px solid var(--line);background:var(--bg1)}
 h1{margin:0;font-size:18px;color:var(--acc)}
 #sum{color:var(--dim)}
 main{display:grid;grid-template-columns:280px 1fr;height:calc(100vh - 57px)}
 #left{border-right:1px solid var(--line);overflow:auto;padding:10px}
 #right{overflow:auto;padding:10px 14px}
 input{width:100%;padding:7px 10px;background:var(--bg2);border:1px solid var(--line);
       border-radius:6px;color:var(--fg);outline:none;margin-bottom:8px}
 .m{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;cursor:pointer}
 .m:hover{background:var(--bg2)}
 .m.on{background:#2a1620;box-shadow:inset 2px 0 0 var(--acc)}
 .big{font-size:17px;min-width:2.5em}
 .num{margin-left:auto;color:var(--dim);font-variant-numeric:tabular-nums}
 table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
 th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
 th{position:sticky;top:0;background:var(--bg1);color:var(--dim);font-weight:600;z-index:1}
 td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){text-align:left}
 tr:hover td{background:var(--bg1)}
 .flag{color:#ffb454}.dot{color:var(--acc)}
 audio{width:230px;height:30px;vertical-align:middle}
</style></head><body>
<header><h1>● 音库索引</h1><span id="sum"></span>
  <span style="margin-left:auto;color:var(--dim)">点左边的音试听 clips\&lt;音&gt;.wav</span></header>
<main><div id="left"><input id="q" placeholder="筛选（假名 / 罗马音 / 来源）"></div>
<div id="right">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
    <span id="cur" class="big"></span><span id="curinfo" style="color:var(--dim)"></span>
    <audio id="au" controls preload="none"></audio>
  </div>
  <table><thead><tr><th>来源</th><th>位置</th><th>起点</th><th>终点</th>
  <th>时长</th><th>置信</th><th>峰值dB</th><th>落差</th><th>标记</th></tr></thead>
  <tbody id="tb"></tbody></table>
</div></main>
<script>
const D = /*__DATA__*/null;
const moras = D.moras, rows = D.rows;
document.getElementById('sum').textContent =
  `唯一 ${moras.length} 种 / 共 ${rows.length} 个音`;
const left = document.getElementById('left'), q = document.getElementById('q');
const tb = document.getElementById('tb'), au = document.getElementById('au');
let sel = null, list = [];
function drawList(){
  const kw = q.value.trim().toLowerCase();
  list = moras.filter(m => !kw || m.mora.includes(kw) ||
                           (m.label || '').toLowerCase().includes(kw));
  [...left.querySelectorAll('.m')].forEach(e => e.remove());
  for (const m of list){
    const d = document.createElement('div');
    d.className = 'm' + (m.mora === sel ? ' on' : '');
    d.innerHTML = `<span class="big">${m.label || m.mora}</span>` +
                  `<span class="num">${m.n} 条 · 干净 ${m.clean}</span>`;
    d.onclick = () => pick(m.mora);
    left.appendChild(d);
  }
}
function pick(mora){
  sel = mora; drawList();
  const cur = moras.find(x => x.mora === mora);
  document.getElementById('cur').textContent = cur ? (cur.label || mora) : mora;
  const rs = rows.filter(r => r.mora === mora);
  const cl = rs.filter(r => !r.flags).length;
  document.getElementById('curinfo').textContent = `${rs.length} 条候选 · 干净 ${cl} 条`;
  au.src = 'clips/' + encodeURIComponent(mora) + '.wav';
  tb.innerHTML = rs.map(r => `<tr>
     <td>${r.source}</td><td>${r.pos}</td>
     <td>${r.start.toFixed(3)}</td><td>${r.end.toFixed(3)}</td>
     <td>${Math.round(r.dur*1000)}ms</td><td>${r.conf.toFixed(2)}</td>
     <td>${r.peak_db.toFixed(1)}</td><td>${r.dip.toFixed(1)}</td>
     <td class="${r.flags?'flag':'dot'}">${r.flags||'·'}</td></tr>`).join('');
}
q.oninput = drawList;
drawList();
if (moras.length) pick(moras[0].mora);
</script></body></html>
"""
