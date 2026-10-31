"""歌詞を実際に音にする（拼句）。

切点表から音を拾って、短いクロスフェードで繋ぐ。
音高は触らない（必要になったら別工程）。
"""

from __future__ import annotations

import csv
import os
from types import SimpleNamespace

import numpy as np

from . import kana as K
from .audio import read_audio, resample, write_wav
from .bank import load_all
from .plan import _score, _substitutes


def load_bank_map(out_dir: str) -> dict[str, list[dict]]:
    data = load_all(out_dir)
    bank: dict[str, list[dict]] = {}
    for name, marks in data.items():
        for m in marks:
            bank.setdefault(m.mora, []).append({
                "mora": m.mora, "source": name,
                "start": m.start, "end": m.end, "dur": m.dur,
                "conf": m.conf, "flags": m.flags, "mark": m,
                "pos": "句头" if m.idx == 0 or (marks[m.idx - 1].phrase != m.phrase) else "句中",
            })
    return bank


def choose_line(bank: dict[str, list[dict]], moras: list[K.Mora],
                allow_sub: bool = True) -> list[dict]:
    """1 行分の音を選ぶ。同じ断片の使い回しは避ける。"""
    used_clips: set = set()
    used_sources: set = set()
    out: list[dict] = []
    prev_vowel = None
    for m in moras:
        cands_pool = bank.get(m.text, [])
        if m.text == "ー" and prev_vowel:
            # 長音は直前のモーラと同じ母音でないと別の音に聞こえる
            same = [c for c in cands_pool
                    if (c["mark"].phones or "").split("/")[0] == prev_vowel]
            if same:
                cands_pool = same
        cands = sorted(cands_pool,
                       key=lambda r: -_score(r, used_clips, used_sources))
        if cands:
            best = cands[0]
            used_clips.add((best["source"], best["start"]))
            used_sources.add(best["source"])
            out.append({"mora": m.text, "pick": best, "kind": "ok", "backup": cands[1:3]})
            prev_vowel = K.vowel_of(m) or prev_vowel
            continue
        if allow_sub:
            subs = [s for s in _substitutes(bank, m.text) if bank.get(s)]
            if subs:
                s = subs[0]
                sc = sorted(bank[s], key=lambda r: -_score(r, used_clips, used_sources))
                best = sc[0]
                used_clips.add((best["source"], best["start"]))
                used_sources.add(best["source"])
                out.append({"mora": m.text, "pick": best, "kind": "sub",
                            "sub_from": s, "backup": sc[1:3]})
                prev_vowel = K.vowel_of(m) or prev_vowel
                continue
        out.append({"mora": m.text, "pick": None, "kind": "missing", "backup": []})
        prev_vowel = K.vowel_of(m) or prev_vowel
    return out


def _rms_db(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    return 20 * np.log10(max(r, 1e-9))


def _fade_in(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    n = min(int(sr * ms / 1000), max(0, len(x)))
    if n <= 0:
        return x
    y = x.copy()
    y[:n] *= np.linspace(0, 1, n, dtype=np.float32)
    return y


def _fade_out(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    n = min(int(sr * ms / 1000), max(0, len(x)))
    if n <= 0:
        return x
    y = x.copy()
    y[-n:] *= np.linspace(1, 0, n, dtype=np.float32)
    return y


def _cut(au, m, head: float, tail: float, sr: int) -> np.ndarray:
    a = max(0.0, m.start - head)
    b = min(au.duration, m.end + tail)
    return au.data[int(a * au.sr):int(b * au.sr)].astype(np.float32)


def _normalize(x: np.ndarray, target_db: float) -> np.ndarray:
    cur = _rms_db(x)
    if cur < -70:
        return x
    g = 10 ** ((target_db - cur) / 20.0)
    return np.clip(x * g, -0.99, 0.99).astype(np.float32)


def _place(out: np.ndarray, seg: np.ndarray, start: int, xf: int) -> None:
    """out の start に seg を置く。start が前の音と重なる場合、先頭 xf サンプルを等パワーで混ぜる。"""
    end = start + len(seg)
    if end > len(out):
        seg = seg[: len(out) - start]
        end = len(out)
    if xf > 0 and start > 0 and len(seg) > xf:
        ramp = np.linspace(0, 1, xf, dtype=np.float32)
        # 等パワー（二乗和が 1）で混ぜると、繋ぎ目が小さくならない
        mix = np.sqrt(1.0 - ramp) * out[start:start + xf] + np.sqrt(ramp) * seg[:xf]
        out[start:start + xf] = mix
        out[start + xf:end] = seg[xf:]
    else:
        out[start:end] = seg


def assemble(out_dir: str, src_dir: str, lyrics_path: str, dst: str = "render",
             head: float = 0.008, tail: float = 0.015, fade_ms: float = 5.0,
             crossfade_ms: float = 8.0, target_db: float = -18.0,
             slot: float | None = None, line_gap: float = 0.45,
             allow_sub: bool = True, limit: int = 0) -> dict:
    bank = load_bank_map(out_dir)
    if not bank:
        raise RuntimeError("音库が空。先に align と bank を実行して")

    raw = open(lyrics_path, encoding="utf-8-sig").read()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if limit:
        lines = lines[:limit]

    out_dir_full = os.path.join(out_dir, dst)
    os.makedirs(out_dir_full, exist_ok=True)

    cache: dict[str, object] = {}
    timeline: list[dict] = []
    renders: list[np.ndarray] = []
    sr_used = 44100
    missing_total: dict[str, int] = {}
    sub_total: dict[str, int] = {}

    for ln, line in enumerate(lines, 1):
        kana = K.normalize(K.to_kana(line))
        moras = [m for m in K.mora_split(kana) if m.kind != "pause"]
        picked = choose_line(bank, moras, allow_sub=allow_sub)

        # 1 行ぶんの長さを見積もる
        segs = []
        for p in picked:
            if not p["pick"]:
                dur = slot or 0.22
                segs.append((p, None, dur))
                continue
            src = p["pick"]["source"]
            if src not in cache:
                cache[src] = read_audio(os.path.join(src_dir, src + ".wav"))
            au = cache[src]
            sr_used = au.sr
            seg = _cut(au, p["pick"]["mark"], head, tail, sr_used)
            if slot and len(seg) > int(slot * sr_used):
                seg = seg[: int(slot * sr_used)]
            segs.append((p, seg, len(seg) / sr_used))

        if not segs:
            continue
        xf = int(crossfade_ms / 1000 * sr_used)
        total = sum(len(s[1]) for s in segs) + xf * 4 + 64
        out = np.zeros(max(1, total), dtype=np.float32)
        cursor = 0
        n_seg = len(segs)
        for i, (p, seg, d) in enumerate(segs):
            xf_here = xf if (i > 0 and p["pick"] and segs[i - 1][0]["pick"]) else 0
            start = 0 if i == 0 else max(0, cursor - xf_here)
            if p["pick"] is None:
                seg = np.zeros(int((slot or 0.22) * sr_used), dtype=np.float32)
                missing_total[p["mora"]] = missing_total.get(p["mora"], 0) + 1
            else:
                seg = seg.copy()
                # 端のフェードは行の最初と最後だけ。繋ぎ目はクロスフェードに任せる
                if i == 0:
                    seg = _fade_in(seg, sr_used, max(fade_ms, 2.0))
                if i == n_seg - 1:
                    seg = _fade_out(seg, sr_used, max(fade_ms, 8.0))
                seg = _normalize(seg, target_db)
                if p["kind"] == "sub":
                    key = p["mora"] + "→" + p.get("sub_from", "")
                    sub_total[key] = sub_total.get(key, 0) + 1
            _place(out, seg, start, xf_here)
            timeline.append({
                "line": ln, "idx": len(timeline) + 1, "mora": p["mora"],
                "kind": p["kind"],
                "source": (p["pick"]["source"] if p["pick"] else ""),
                "src_start": (round(p["pick"]["start"], 3) if p["pick"] else ""),
                "src_end": (round(p["pick"]["end"], 3) if p["pick"] else ""),
                "conf": (round(p["pick"]["conf"], 2) if p["pick"] else ""),
                "sub_from": p.get("sub_from", ""),
                "out_start": round(start / sr_used, 3),
                "out_end": round((start + len(seg)) / sr_used, 3),
            })
            cursor = start + len(seg)
        out = out[:cursor]

        name = f"line{ln:03d}.wav"
        write_wav(os.path.join(out_dir_full, name), out, sr_used)
        renders.append(out)
        renders.append(np.zeros(int(line_gap * sr_used), dtype=np.float32))

    full = np.concatenate(renders) if renders else np.zeros(1, dtype=np.float32)
    write_wav(os.path.join(out_dir_full, "all.wav"), full, sr_used)

    with open(os.path.join(out_dir, "timeline.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["line", "idx", "mora", "kind", "source",
                                          "src_start", "src_end", "conf", "sub_from",
                                          "out_start", "out_end"])
        w.writeheader()
        for r in timeline:
            w.writerow(r)

    return {
        "lines": len(renders) // 2, "segments": len(timeline),
        "duration": len(full) / sr_used, "dir": out_dir_full,
        "missing": missing_total, "subs": sub_total,
    }


# ====================================================================== 人力试作
# edit.json の items（source / start / end / kind / lead）を直接レンダリングする。
# 「人力试作」タブの試聴と書き出しは両方ここを通すので、音の作り方が一致する。

def _item_missing(it: dict) -> bool:
    return (not str(it.get("source") or "").strip()) or (it.get("kind") == "missing")


def _source_audio(cache: dict, src_dir: str, source: str):
    key = str(source or "")
    if key not in cache:
        cache[key] = read_audio(os.path.join(src_dir, key + ".wav"))
    return cache[key]


def _explicit_at(it: dict) -> float | None:
    """item に明示的なタイムライン位置（秒）があれば返す。"""
    v = (it or {}).get("at", None)
    if v is None or v == "":
        return None
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return None


def render_items(items: list[dict], src_dir: str, head: float = 0.008,
                 tail: float = 0.015, fade_ms: float = 5.0,
                 crossfade_ms: float = 8.0, target_db: float = -18.0,
                 slot: float = 0.22, cache: dict | None = None,
                 sr_out: int | None = None
                 ) -> tuple[np.ndarray, int, list[dict]]:
    """items 1 本ぶんを波形にする。

    item: {"mora","source","start","end","kind","sub_from","lead","at",...}
      lead … 直前の音との間に空ける無音（秒）。0 ならクロスフェードで繋ぐ。
      at   … タイムライン上の絶対位置（秒）。ある場合は lead より優先する。
             自由配置（隙間・重なり）を許すため、重なった部分は加算ミックスする。
    戻り値: (audio, sr, 各 item の out_start/out_end 付きメタ)
    """
    cache = cache if cache is not None else {}
    items = list(items or [])

    if sr_out:
        sr_out = int(sr_out)
    else:
        sr_out = 44100
        for it in items:
            if not _item_missing(it):
                try:
                    sr_out = int(_source_audio(cache, src_dir, it["source"]).sr) or 44100
                    break
                except Exception:
                    continue

    xf = int(max(0.0, crossfade_ms) / 1000 * sr_out)
    segs: list[np.ndarray] = []
    for it in items:
        if _item_missing(it):
            segs.append(np.zeros(max(1, int(slot * sr_out)), dtype=np.float32))
            continue
        try:
            au = _source_audio(cache, src_dir, it["source"])
            a = float(it.get("start") or 0.0)
            b = float(it.get("end") or 0.0)
            if b <= a:
                seg = np.zeros(1, dtype=np.float32)
            else:
                seg = _cut(au, SimpleNamespace(start=a, end=b), head, tail, au.sr)
                if au.sr != sr_out:
                    seg = resample(seg, au.sr, sr_out)
        except Exception:
            seg = np.zeros(max(1, int(slot * sr_out)), dtype=np.float32)
        if len(seg) == 0:
            seg = np.zeros(1, dtype=np.float32)
        segs.append(np.asarray(seg, dtype=np.float32).copy())

    if not segs:
        return np.zeros(1, dtype=np.float32), sr_out, []

    leads = [max(0.0, float(it.get("lead") or 0.0)) for it in items]

    # 1 パス目: 各 item の開始サンプルを決める（at があれば絶対配置）。
    starts: list[int] = []
    cursor = 0
    for i, (it, seg) in enumerate(zip(items, segs)):
        at = _explicit_at(it)
        if at is not None:
            start = int(round(at * sr_out))
        elif i == 0:
            start = 0
        elif leads[i] > 0:
            start = cursor + int(leads[i] * sr_out)
        else:
            missing = _item_missing(it)
            prev_missing = _item_missing(items[i - 1])
            xf_here = xf if (not missing and not prev_missing) else 0
            start = max(0, cursor - xf_here)
        starts.append(start)
        cursor = max(cursor, start + len(seg))

    out = np.zeros(max(1, cursor + 64), dtype=np.float32)
    prev_end = 0
    meta: list[dict] = []
    for i, (it, seg) in enumerate(zip(items, segs)):
        start = starts[i]
        missing = _item_missing(it)
        prev_missing = i > 0 and _item_missing(items[i - 1])
        seg = seg.copy()
        if i == 0:
            seg = _fade_in(seg, sr_out, max(fade_ms, 2.0))
        if i == len(segs) - 1:
            seg = _fade_out(seg, sr_out, max(fade_ms, 8.0))
        if not missing:
            seg = _normalize(seg, target_db)

        explicit = _explicit_at(it) is not None
        overlap = start < prev_end
        if explicit and overlap:
            ov = prev_end - start
            if (ov <= xf and not missing and not prev_missing
                    and leads[i] <= 0 and start > 0):
                # 小さな重なりは等パワークロスフェード
                _place(out, seg, start, min(xf, int(ov)))
            else:
                # 自由配置の重なりは音を消さず加算する
                end = min(len(out), start + len(seg))
                if end > start:
                    out[start:end] += seg[: end - start]
        else:
            xf_here = xf if (i > 0 and not missing and not prev_missing
                             and leads[i] <= 0 and start > 0) else 0
            _place(out, seg, start, xf_here)

        prev_end = max(prev_end, start + len(seg))
        row = dict(it)
        row.update({
            "out_start": round(start / sr_out, 3),
            "out_end": round((start + len(seg)) / sr_out, 3),
            "missing": missing,
        })
        meta.append(row)

    out = np.clip(out[:cursor], -1.0, 1.0).astype(np.float32)
    return out, sr_out, meta


def audible_tracks(tracks: list[dict]) -> list[dict]:
    """Solo / Mute を反映した「鳴らす・書き出す」トラック一覧。

    Solo が 1 本でもあれば Solo だけ。無ければ Mute されていない全部。
    """
    tracks = [t for t in (tracks or []) if isinstance(t, dict)]
    solo = [t for t in tracks if t.get("solo")]
    if solo:
        return solo
    return [t for t in tracks if not t.get("mute")]


def render_edit(out_dir: str, src_dir: str, tracks: list[dict], dst: str = "render",
                head: float = 0.008, tail: float = 0.015, fade_ms: float = 5.0,
                crossfade_ms: float = 8.0, target_db: float = -18.0,
                slot: float = 0.22, line_gap: float = 0.45) -> dict:
    """edit.json の tracks を書き出す。出力は assemble() と同じ形。

    Solo / Mute はここで反映する（Solo があれば Solo だけ、無ければ Mute 以外）。
    """
    out_dir_full = os.path.join(out_dir, dst)
    os.makedirs(out_dir_full, exist_ok=True)

    cache: dict = {}
    timeline: list[dict] = []
    renders: list[np.ndarray] = []
    sr_used = 44100
    missing_total: dict[str, int] = {}
    sub_total: dict[str, int] = {}
    lines = 0

    for ln, tr in enumerate(audible_tracks(tracks), 1):
        items = list((tr or {}).get("items") or [])
        if not items:
            continue
        arr, sr, metas = render_items(
            items, src_dir, head=head, tail=tail, fade_ms=fade_ms,
            crossfade_ms=crossfade_ms, target_db=target_db, slot=slot, cache=cache,
            sr_out=None if lines == 0 else sr_used)
        sr_used = sr
        lines += 1
        write_wav(os.path.join(out_dir_full, f"line{ln:03d}.wav"), arr, sr)
        renders.append(arr)
        renders.append(np.zeros(int(line_gap * sr), dtype=np.float32))

        for it, mm in zip(items, metas):
            kind = it.get("kind") or ("missing" if mm.get("missing") else "ok")
            mora = str(it.get("mora") or "")
            conf = it.get("conf", "")
            if isinstance(conf, (int, float)):
                conf = round(float(conf), 2)
            if mm.get("missing"):
                missing_total[mora] = missing_total.get(mora, 0) + 1
            elif kind == "sub":
                key = mora + "→" + str(it.get("sub_from") or "")
                sub_total[key] = sub_total.get(key, 0) + 1
            timeline.append({
                "line": ln, "idx": len(timeline) + 1, "mora": mora,
                "kind": kind,
                "source": "" if mm.get("missing") else str(it.get("source") or ""),
                "src_start": "" if mm.get("missing") else round(float(it.get("start") or 0), 3),
                "src_end": "" if mm.get("missing") else round(float(it.get("end") or 0), 3),
                "conf": conf,
                "sub_from": it.get("sub_from", ""),
                "out_start": mm["out_start"],
                "out_end": mm["out_end"],
            })

    full = np.concatenate(renders) if renders else np.zeros(1, dtype=np.float32)
    write_wav(os.path.join(out_dir_full, "all.wav"), full, sr_used)

    with open(os.path.join(out_dir, "timeline.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        fields = ["line", "idx", "mora", "kind", "source", "src_start", "src_end",
                  "conf", "sub_from", "out_start", "out_end"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in timeline:
            w.writerow(row)

    return {
        "lines": lines, "segments": len(timeline),
        "duration": len(full) / sr_used, "dir": out_dir_full,
        "missing": missing_total, "subs": sub_total,
    }

