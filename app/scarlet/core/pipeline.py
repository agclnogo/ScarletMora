"""素材 → 切点表。整列パイプライン本体。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np

from . import kana as K
from .audio import frame_db, read_audio, resample
from .align import Aligner, greedy_decode, viterbi_align
from .store import Mark, write_audacity_labels, write_marks

FRAME = 0.02          # hubert base @16k


def parse_transcript(path: str) -> list[tuple[str, str]]:
    """「ファイル名 → 原文」の並びを読む。

    対応形式:
      A) 1 行目=ファイル名 / 2 行目=原文（空行区切り）
      B) ファイル名<TAB>原文
    """
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp932", "utf-16"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("テキストをデコードできない")
    text = text.replace("\ufeff", "")

    pairs: list[tuple[str, str]] = []
    lines = [l.strip() for l in text.splitlines()]
    if any("\t" in l for l in lines):
        for l in lines:
            if not l or "\t" not in l:
                continue
            a, b = l.split("\t", 1)
            pairs.append((a.strip(), b.strip()))
        return pairs

    i = 0
    while i < len(lines):
        if not lines[i]:
            i += 1
            continue
        head = lines[i]
        i += 1
        body = ""
        if i < len(lines) and lines[i]:
            body = lines[i]
            i += 1
        pairs.append((head, body))
    return pairs


@dataclass
class FileResult:
    name: str
    text: str
    kana: str
    duration: float
    avg_conf: float
    decoded: str
    marks: list[Mark]
    sim: float = 0.0


HA_WA_MARGIN = 0.9      # 「は(ha)」と「わ(wa)」を音響で判定するときの差（nats）


def _hear_ha_wa(span, logp: np.ndarray, tok2id: dict) -> str | None:
    """子音区間の音響から は / わ を見分ける。

    テキスト側で助詞「は」→「わ」に直しても、実際に「ハ」と言っている
    （逆に「ワ」と言っている）ことがあるので、モデルの事後確率で確かめる。
    判断がつかないときは None（テキスト側の判定をそのまま使う）。
    """
    ih, iw = tok2id.get("h"), tok2id.get("w")
    if ih is None or iw is None:
        return None
    a, b = int(span.start), int(span.end)
    if b - a < 2:
        return None
    seg = logp[a:b]
    dh = float(seg[:, ih].mean())
    dw = float(seg[:, iw].mean())
    if dw - dh > HA_WA_MARGIN:
        return "わ"
    if dh - dw > HA_WA_MARGIN:
        return "は"
    return None


def _flags(m: Mark, level_db: float, dip: float) -> list[str]:
    f: list[str] = []
    if m.dur < 0.045:
        f.append("短")
    if m.dur > 0.50:
        f.append("長")
    if m.conf < 0.40:
        f.append("低信")
    if m.peak_db < level_db - 28:
        f.append("弱")
    if m.mora == "っ" and dip < 3.0:
        f.append("っ無閉鎖")
    return f


def analyze(name: str, text: str, wav_path: str, aligner: Aligner, shift: float = 0.020) -> FileResult:
    au = read_audio(wav_path)
    wav16 = resample(au.data, au.sr, aligner.sr)

    kana = K.normalize(K.to_kana(text))
    moras = K.mora_split(kana)
    moras = [m for m in moras if m.kind != "pause"]

    # 音素列と「どの音素がどのモーラか」の対応表。
    # 句の切れ目には pau を挟む（挟まないと休符が前後のモーラに食われる）。
    phones: list[str] = []
    owner: list[int] = []
    for mi, m in enumerate(moras):
        if mi > 0 and m.phrase != moras[mi - 1].phrase:
            phones.append("pau")
            owner.append(-1)
        for p in m.phones:
            phones.append(p)
            owner.append(mi)

    logp = aligner.log_probs(wav16)
    ids = aligner.phones_to_ids(phones)
    if len(ids) != len(phones):
        kept = [k for k, p in enumerate(phones) if p in aligner.tok2id]
        phones = [phones[k] for k in kept]
        owner = [owner[k] for k in kept]
    spans, total, _path = viterbi_align(logp, ids)

    db, hop = frame_db(au.data, au.sr, 10.0)
    db16, hop16 = frame_db(wav16, aligner.sr, 10.0)
    speech = float(np.percentile(db16[db16 > -70], 90)) if np.any(db16 > -70) else -40.0

    marks: list[Mark] = []
    for mi, m in enumerate(moras):
        idxs = [k for k, o in enumerate(owner) if o == mi]
        if not idxs:
            continue
        a_f, b_f = spans[idxs[0]].start, spans[idxs[-1]].end
        start = a_f * FRAME
        end = min(b_f * FRAME, len(au.data) / au.sr)
        conf = float(np.mean([spans[k].conf for k in idxs]))

        # は / わ は「意味（助詞かどうか）」と「実際の音」の両方で決める。
        mora_text, mora_phones = m.text, list(m.phones)
        heard = None
        if mora_text in ("は", "わ") and len(idxs) >= 2:
            heard = _hear_ha_wa(spans[idxs[0]], logp, aligner.tok2id)
            if heard and heard != mora_text:
                mora_text = heard
                mora_phones = ["w", "a"] if heard == "わ" else ["h", "a"]
            else:
                heard = None

        # 子音が終わる位置（母音立ち上がり）
        on = start
        if len(idxs) >= 2:
            on = spans[idxs[0]].end * FRAME
        vc = -1.0
        for k in reversed(idxs):
            if phones[k] in K.VOWELS:
                sp = spans[k]
                vc = (sp.start + sp.end) / 2 * FRAME
                break

        s_i, e_i = int(start * 1000 / 10), int(end * 1000 / 10)
        seg = db16[max(0, s_i):max(1, e_i)]
        peak = float(seg.max()) if len(seg) else -120.0

        # 境界の落ち込み量（前後 ±30ms の最小値と、両隣の平均の差）
        c = int(end * 100)
        lo, hi = max(0, c - 3), min(len(db16), c + 3)
        near = float(db16[lo:hi].min()) if hi > lo else -60.0
        left = float(db16[max(0, c - 10):lo].mean()) if lo > max(0, c - 10) else near
        dip = min(peak, left) - near

        mark = Mark(idx=len(marks), mora=mora_text, phones="/".join(mora_phones),
                    start=start, end=end, conf=conf, on=on, vowel_center=vc,
                    peak_db=peak, dip=float(dip), phrase=m.phrase)
        bits = _flags(mark, speech, dip)
        if heard:
            bits.append("听" + heard)
        mark.flags = ",".join(bits)
        marks.append(mark)

    # 境界を少し後ろへ（切り出しが「早い」と言われたため）。
    # 連続している音の境目だけ動かし、隙間（本物の休符）はそのまま残す。
    if shift:
        for i in range(len(marks) - 1):
            if marks[i + 1].start - marks[i].end < 0.005:
                t = marks[i].end + shift
                t = min(t, marks[i + 1].end - 0.02)
                if t > marks[i].start + 0.02:
                    marks[i].end = t
                    marks[i + 1].start = t

    # 1 つのモーラが長い休符をまたいでいる（子音と母音が離れすぎ）場合は印を付ける。
    # 多くの場合、原文に句読点が無いのに音声では息継ぎしている箇所。
    for m in marks:
        if m.dur > 0.6:
            m.flags = (m.flags + ",跨停顿") if m.flags else "跨停顿"

    # 転写の健全性チェック：モデルの貪欲デコードと目標音素列の一致度。
    # 有声/無声母音（I/U）や cl・pau の有無で差が出るので、正規化してから比べる。
    import difflib

    def canon(seq):
        return [{"I": "i", "U": "u"}.get(p, p) for p in seq
                if p not in ("SOS", "EOS", "UNK", "PAD", "pau", "sil", "cl")]

    dec = greedy_decode(logp, aligner.id2tok, aligner.blank_id)
    sim = difflib.SequenceMatcher(None, canon(phones), canon(dec)).ratio() if phones else 0.0

    avg = float(np.mean([m.conf for m in marks])) if marks else 0.0
    fr = FileResult(name=name, text=text, kana=kana, duration=len(au.data) / au.sr,
                    avg_conf=avg, decoded=" ".join(dec), sim=float(sim), marks=marks)
    fr._logp = logp            # 後段（自己再構成チェック）用に保持
    fr._score = total
    return fr


def save_file_result(fr: FileResult, out_dir: str) -> None:
    write_marks(os.path.join(out_dir, "marks", f"{fr.name}.csv"), fr.marks)
    write_audacity_labels(os.path.join(out_dir, "labels", f"{fr.name}.txt"), fr.marks)


def project_json(files: list[FileResult], src_dir: str, out_dir: str) -> dict:
    return {
        "src_dir": os.path.abspath(src_dir),
        "files": [
            {
                "name": f.name,
                "duration": round(f.duration, 3),
                "text": f.text,
                "kana": f.kana,
                "avg_conf": round(f.avg_conf, 3),
                "marks": [m.to_row() for m in f.marks],
            }
            for f in files
        ],
    }


def save_project(files: list[FileResult], src_dir: str, out_dir: str) -> str:
    path = os.path.join(out_dir, "project.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(project_json(files, src_dir, out_dir), f, ensure_ascii=False)
    return path
