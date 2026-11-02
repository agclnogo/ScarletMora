"""解析ワーカー（外部 Python 用）。

    python worker.py align --library "<音库フォルダ>" [--only a,b] [--shift 0.02]

torch / transformers を使うので exe には入れず、システムの Python で走らせる。
GUI はこのスクリプトをサブプロセスで起動して標準出力を読む。
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

from scarlet import paths                      # noqa: E402
from scarlet.library import Library            # noqa: E402


def emit(msg: str) -> None:
    print(msg, flush=True)


def cmd_align(a) -> int:
    lib = Library(a.library)
    lib.load()

    # --model（GUI が選択した場所）→ settings.json → models\ の順で探す
    model_dir = paths.normalize_model_path(a.model) or paths.find_model_dir()
    problem = paths.model_problem(model_dir)
    if problem:
        # GUI 側でも同じ案内を出すが、worker を直接叩いたとき用にここでも出す
        emit(f"ERROR モデルが無い: {model_dir or paths.default_model_dir()}（{problem}）")
        emit(f"ERROR {paths.MODEL_DOWNLOAD_CMD}")
        emit(f"ERROR 下载页: {paths.MODEL_URL}")
        return 2

    want = [s for s in (a.only or "").split(",") if s.strip()]
    tracks = [t for t in lib.tracks if (t.get("text") or "").strip()]
    if want:
        tracks = [t for t in tracks if t["name"] in want]
    if not want and a.pending:
        tracks = [t for t in tracks if not os.path.exists(lib.marks_path(t["name"]))]
    if not tracks:
        emit("DONE 0 本（対象なし）")
        return 0

    from scarlet.core.align import Aligner
    from scarlet.core.pipeline import analyze, save_file_result

    emit(f"STAGE 加载模型（torch / transformers，首次约 10~30 秒）…")
    emit(f"対象: {len(tracks)} 本")
    aligner = Aligner(model_dir)
    emit("STAGE 模型就绪 "
         f"device={getattr(aligner, 'device', '?')} 語彙={len(getattr(aligner, 'tok2id', {}))}")

    ok = 0
    mora_total = 0
    for i, t in enumerate(tracks, 1):
        name = t["name"]
        wav = lib.audio_path(t)
        emit(f"PROGRESS {i} {len(tracks)} {name}")
        if not os.path.isfile(wav):
            emit(f"WARN 音声が無い: {name}")
            continue
        try:
            fr = analyze(name, t.get("text") or "", wav, aligner, shift=a.shift)
            save_file_result(fr, lib.path)
            t["status"] = "analyzed"
            t["avg_conf"] = round(fr.avg_conf, 3)
            t["sim"] = round(fr.sim, 3)
            t["duration"] = round(fr.duration, 3)
            lib.save()
            ok += 1
            mora_total += len(fr.marks)
            emit(f"OK {name} mora={len(fr.marks)} conf={fr.avg_conf:.2f} 一致={fr.sim:.2f}")
        except Exception as e:  # noqa: BLE001
            emit(f"WARN 失敗 {name}: {e}")
            emit(traceback.format_exc().replace("\n", " | ")[-400:])

    # ---- 後処理（索引 → 切片 → 報告）。workdir と同じ一式が残るようにする。
    emit("STAGE 建索引（bank.csv / bank_summary / report.txt）…")
    try:
        lib.build_bank()
        emit("STAGE 索引を更新した")
    except Exception as e:  # noqa: BLE001
        emit(f"WARN 索引の更新に失敗: {e}")

    clips_n = 0
    if not a.no_clips:
        emit("STAGE 导出切片（clips / clips_all）…")
        try:
            r = lib.export_clips(progress=lambda i, total: emit(f"CLIP {i} {total}"))
            clips_n = r.get("total", 0)
            emit(f"STAGE 切片 {clips_n} 個 / {r.get('mora', 0)} 種")
        except Exception as e:  # noqa: BLE001
            emit(f"WARN 切片の書き出しに失敗: {e}")

    emit(f"SUM {ok} {mora_total} {clips_n}")
    emit(f"DONE {ok} 本")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scarlet-worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("align")
    p.add_argument("--library", required=True)
    p.add_argument("--only", default="")
    p.add_argument("--shift", type=float, default=0.02)
    p.add_argument("--model", default="")
    p.add_argument("--pending", action="store_true", help="未解析のものだけ")
    p.add_argument("--no-clips", action="store_true", help="切片を書き出さない（速い）")
    p.set_defaults(func=cmd_align)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
