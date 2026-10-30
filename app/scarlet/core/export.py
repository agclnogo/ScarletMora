"""モーラを実際に wav として切り出す。

方針: 切り足りないより切りすぎのほうがマシ。前後に余白を多めに取り、
端は 3ms のフェードで押さえる。
"""

from __future__ import annotations

import csv
import os

import numpy as np

from .audio import read_audio, write_wav
from .bank import load_all

PENALTY = {"低信": 0.40, "短": 0.25, "長": 0.15, "弱": 0.30, "跨停顿": 0.50, "っ無閉鎖": 0.10}


def _score(m) -> float:
    s = float(m.conf)
    for f in (m.flags or "").split(","):
        s -= PENALTY.get(f.strip(), 0.0)
    return s


def _fade(x: np.ndarray, sr: int, ms: float = 3.0) -> np.ndarray:
    n = max(1, int(sr * ms / 1000))
    n = min(n, len(x) // 2) if len(x) > 2 else 0
    if n <= 0:
        return x
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y = x.copy()
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
    return y


def export_clips(out_dir: str, src_dir: str, dst: str = "clips",
                 pad_head: float = 0.040, pad_tail: float = 0.070,
                 shift: float = 0.0, all_takes: bool = True,
                 fade_ms: float = 3.0, progress=None) -> dict:
    """全モーラを書き出す。

    pad_head/pad_tail: 前後の余白（秒）。切りすぎ防止。
    shift: 境界全体を後ろにずらす量（「少し早い」対策）。
    all_takes: True なら clips_all/<mora>/ に候補を全部、clips_best/ に最良 1 本。
    progress: progress(done, total) を渡すと進捗を通知する。
    """
    data = load_all(out_dir)
    all_dir = os.path.join(out_dir, dst + "_all")
    best_dir = os.path.join(out_dir, dst)
    os.makedirs(all_dir, exist_ok=True)
    os.makedirs(best_dir, exist_ok=True)

    # 前回の切片を消してから書き直す（読みが変わると は→わ で名前も変わるため）
    for d in (all_dir, best_dir):
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith(".wav"):
                    try:
                        os.remove(os.path.join(root, fn))
                    except OSError:
                        pass

    rows: list[dict] = []
    best: dict[str, tuple[float, dict]] = {}
    total_mora = sum(len(v) for v in data.values())

    def tick() -> None:
        if progress is not None:
            try:
                progress(len(rows), total_mora)
            except Exception:
                pass

    for name, marks in data.items():
        wav_path = os.path.join(src_dir, name + ".wav")
        if not os.path.exists(wav_path):
            continue
        au = read_audio(wav_path)
        sr = au.sr
        for m in marks:
            a = max(0.0, m.start + shift - pad_head)
            b = min(au.duration, m.end + shift + pad_tail)
            if b - a < 0.03:
                continue
            i0, i1 = int(a * sr), int(b * sr)
            seg = _fade(au.data[i0:i1], sr, fade_ms)
            sc = _score(m)
            key = m.mora
            safe = key.replace("/", "_")
            if all_takes:
                d = os.path.join(all_dir, safe)
                os.makedirs(d, exist_ok=True)
                fn = f"{safe}_{len(rows) % 1000:03d}_{name}_{m.start:.3f}.wav"
                write_wav(os.path.join(d, fn), seg, sr)
            row = {
                "mora": key, "source": name, "start": round(a, 3), "end": round(b, 3),
                "dur": round(b - a, 3), "conf": round(m.conf, 3), "score": round(sc, 3),
                "flags": m.flags or "", "best": 0,
            }
            rows.append(row)
            if key not in best or sc > best[key][0]:
                best[key] = (sc, {"row": row, "audio": seg, "sr": sr})
            if len(rows) % 50 == 0:
                tick()

    for mora, (sc, item) in best.items():
        r = item["row"]
        r["best"] = 1
        write_wav(os.path.join(best_dir, f"{mora.replace('/', '_')}.wav"), item["audio"], item["sr"])

    with open(os.path.join(out_dir, "clips.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["mora", "source", "start", "end", "dur",
                                          "conf", "score", "flags", "best"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    return {"total": len(rows), "mora": len(best), "dir_all": all_dir, "dir_best": best_dir}
