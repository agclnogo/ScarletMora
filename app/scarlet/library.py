"""音库（ライブラリ）: 素材セット 1 つ = フォルダ 1 つ。

    libraries/<名前>/
        library.json      … 目録。ここが唯一の真実。
        audio/            … 音声（取り込み時にコピー or 元の場所を参照）
        transcript.txt    … 取り込んだ原文の控え（あれば）
        marks/ labels/    … 切点表 / Audacity ラベル
        project.json      … 校对 UI 用（毎回 marks から作り直す）
        index.html        … 音库索引（bank から生成）
        clips/ clips_all/ … 書き出したモーラ
        render/ timeline.csv

library.json の形:
    {
      "schema": 1,
      "name": "缪缪语音",
      "audio_mode": "copy",            # copy | reference
      "created": "2026-10-26 19:00:00",
      "updated": "...",
      "notes": "",
      "settings": {"shift": 0.02, "pad_head": 0.04, "pad_tail": 0.07},
      "tracks": [
        {"name": "任命助理", "audio": "audio/任命助理.wav",
         "text": "...", "duration": 18.34, "status": "analyzed",
         "avg_conf": 0.61, "added": "..."}
      ]
    }
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import time
import wave

from . import paths
from .core import assemble as A
from .core import bank as B
from .core import kana as K
from .core import plan as P
from .core import export as E
from .core.store import read_marks

MANIFEST = "library.json"
SCHEMA = 1
SUBDIRS = ("audio", "marks", "labels", "clips", "clips_all", "render", "plan")
AUDIO_EXTS = (".wav",)

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def safe_name(name: str) -> str:
    """Windows で使えない文字を落とす（日本語・中国語はそのまま残す）。"""
    s = _BAD.sub("_", (name or "").strip()).strip(" .")
    return s or "unnamed"


def wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


class Library:
    # ------------------------------------------------------------------ 生成
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self._m: dict | None = None

    # ------------------------------------------------------------- 目録 I/O
    @property
    def manifest_path(self) -> str:
        return os.path.join(self.path, MANIFEST)

    def load(self, reload: bool = False) -> dict:
        if self._m is not None and not reload:
            return self._m
        try:
            with open(self.manifest_path, encoding="utf-8") as f:
                self._m = json.load(f)
        except FileNotFoundError:
            self._m = {"schema": SCHEMA, "name": os.path.basename(self.path),
                       "audio_mode": "copy", "created": now(), "updated": now(),
                       "notes": "", "settings": {}, "tracks": []}
        except Exception:
            self._m = {"schema": SCHEMA, "name": os.path.basename(self.path),
                       "audio_mode": "copy", "created": now(), "updated": now(),
                       "notes": "", "settings": {}, "tracks": []}
        self._m.setdefault("tracks", [])
        self._m.setdefault("settings", {})
        self._m.setdefault("audio_mode", "copy")
        self._m.setdefault("name", os.path.basename(self.path))
        return self._m

    def save(self) -> None:
        m = self.load()
        m["updated"] = now()
        self.ensure_dirs()
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.manifest_path)

    def ensure_dirs(self) -> None:
        for d in SUBDIRS:
            os.makedirs(os.path.join(self.path, d), exist_ok=True)

    # ------------------------------------------------------------ プロパティ
    @property
    def name(self) -> str:
        return self.load().get("name") or os.path.basename(self.path)

    @name.setter
    def name(self, v: str) -> None:
        self.load()["name"] = v
        self.save()

    @property
    def audio_mode(self) -> str:
        return self.load().get("audio_mode", "copy")

    @property
    def notes(self) -> str:
        return self.load().get("notes", "")

    @notes.setter
    def notes(self, v: str) -> None:
        self.load()["notes"] = v
        self.save()

    @property
    def settings(self) -> dict:
        s = self.load()["settings"]
        s.setdefault("shift", float(paths.load_settings().get("shift", 0.02)))
        s.setdefault("pad_head", 0.04)
        s.setdefault("pad_tail", 0.07)
        return s

    @property
    def audio_dir(self) -> str:
        d = os.path.join(self.path, "audio")
        os.makedirs(d, exist_ok=True)
        return d

    @property
    def tracks(self) -> list[dict]:
        return self.load()["tracks"]

    def track(self, name: str) -> dict | None:
        for t in self.tracks:
            if t.get("name") == name:
                return t
        return None

    def audio_path(self, track) -> str:
        """track(dict) か名前から、実際の wav の絶対パスを返す。"""
        t = self.track(track) if isinstance(track, str) else track
        if not t:
            return ""
        rel = t.get("audio") or ""
        if os.path.isabs(rel):
            return rel
        return os.path.join(self.path, rel)

    def marks_path(self, name: str) -> str:
        return os.path.join(self.path, "marks", f"{safe_name(name)}.csv")

    # ---------------------------------------------------------------- 統計
    def stats(self) -> dict:
        tracks = self.tracks
        mora_total = 0
        mora_set: set[str] = set()
        analyzed = 0
        flagged = 0
        for t in tracks:
            mp = self.marks_path(t["name"])
            if not os.path.exists(mp):
                continue
            analyzed += 1
            try:
                marks = read_marks(mp)
            except Exception:
                continue
            mora_total += len(marks)
            for m in marks:
                mora_set.add(m.mora)
                if m.flags:
                    flagged += 1
        size = 0
        for t in tracks:
            try:
                size += os.path.getsize(self.audio_path(t))
            except OSError:
                pass
        return {
            "name": self.name,
            "path": self.path,
            "audio_mode": self.audio_mode,
            "tracks": len(tracks),
            "analyzed": analyzed,
            "mora_total": mora_total,
            "mora_kind": len(mora_set),
            "flagged": flagged,
            "duration": round(sum(float(t.get("duration") or 0) for t in tracks), 1),
            "size_mb": round(size / 1048576, 1),
            "updated": self.load().get("updated", ""),
        }

    # ---------------------------------------------------------------- 生成/一覧
    @classmethod
    def create(cls, name: str, audio_mode: str = "copy", notes: str = "") -> "Library":
        base = safe_name(name)
        root = paths.libraries_dir()
        path = os.path.join(root, base)
        i = 2
        while os.path.exists(os.path.join(path, MANIFEST)):
            path = os.path.join(root, f"{base}_{i}")
            i += 1
        os.makedirs(path, exist_ok=True)
        lib = cls(path)
        lib._m = {
            "schema": SCHEMA,
            "name": base,
            "audio_mode": audio_mode if audio_mode in ("copy", "reference") else "copy",
            "created": now(),
            "updated": now(),
            "notes": notes,
            "settings": {},
            "tracks": [],
        }
        lib.ensure_dirs()
        lib.save()
        paths.set_setting("last_library", path)
        return lib

    @classmethod
    def list_all(cls) -> list["Library"]:
        out = []
        for d in sorted(glob.glob(os.path.join(paths.libraries_dir(), "*"))):
            if os.path.isfile(os.path.join(d, MANIFEST)):
                out.append(cls(d))
        return out

    @classmethod
    def open_last(cls) -> "Library | None":
        p = (paths.load_settings().get("last_library") or "").strip()
        if p and os.path.isfile(os.path.join(p, MANIFEST)):
            return cls(p)
        libs = cls.list_all()
        return libs[0] if libs else None

    def touch(self) -> None:
        paths.set_setting("last_library", self.path)

    # ---------------------------------------------------------------- 取り込み
    def import_items(self, items: list[dict], copy_audio: bool = True,
                     on_conflict: str = "skip") -> dict:
        """items: [{"name": str, "text": str, "audio": 絶対パス}]

        copy_audio=True なら audio/ に複製して音库は自己完結。
        False なら元パスを記録するだけ（reference）。
        on_conflict: skip | replace
        """
        self.ensure_dirs()
        m = self.load()
        mode = "copy" if copy_audio else "reference"
        m["audio_mode"] = mode

        added, replaced, skipped, failed = [], [], [], []
        for it in items:
            name = safe_name(it.get("name") or os.path.splitext(os.path.basename(it.get("audio", "")))[0])
            src = it.get("audio") or ""
            text = (it.get("text") or "").strip()
            old = self.track(name)
            if old is not None and on_conflict == "skip":
                if text and not (old.get("text") or "").strip():
                    old["text"] = text
                    self.save()
                skipped.append(name)
                continue

            target_rel, target_abs = "", ""
            if src and os.path.isfile(src):
                if copy_audio:
                    target_abs = os.path.join(self.audio_dir, f"{name}.wav")
                    if os.path.abspath(src) != os.path.abspath(target_abs):
                        shutil.copy2(src, target_abs)
                    target_rel = os.path.join("audio", f"{name}.wav").replace("\\", "/")
                else:
                    target_abs = os.path.abspath(src)
                    target_rel = target_abs
            elif old is not None:
                # 音声は無いが原文だけ差し替えたい場合
                target_rel, target_abs = old.get("audio", ""), self.audio_path(old)
            else:
                failed.append({"name": name, "reason": "音声が見つからない"})
                continue

            dur = wav_duration(target_abs)
            rec = {
                "name": name,
                "audio": target_rel,
                "text": text or (old or {}).get("text", ""),
                "duration": round(dur, 3),
                "status": "analyzed" if os.path.exists(self.marks_path(name)) else "imported",
                "avg_conf": (old or {}).get("avg_conf", 0),
                "added": (old or {}).get("added") or now(),
            }
            if old is None:
                m["tracks"].append(rec)
                added.append(name)
            else:
                old.update(rec)
                replaced.append(name)

        self.save()
        if added or replaced:
            self.write_project()
        return {"added": added, "replaced": replaced, "skipped": skipped,
                "failed": failed, "mode": mode}

    def save_transcript_copy(self, pairs: list[tuple[str, str]]) -> str:
        path = os.path.join(self.path, "transcript.txt")
        with open(path, "w", encoding="utf-8") as f:
            for name, text in pairs:
                f.write(f"{name}\t{text}\n")
        return path

    # ---------------------------------------------------------------- 再生成
    def write_project(self) -> str:
        """marks/*.csv と目録から project.json（校对 UI の入力）を作り直す。"""
        files = []
        for t in self.tracks:
            name = t["name"]
            text = t.get("text") or ""
            marks = []
            mp = self.marks_path(name)
            if os.path.exists(mp):
                try:
                    marks = read_marks(mp)
                except Exception:
                    marks = []
            try:
                kana = K.normalize(K.to_kana(text)) if text else ""
            except Exception:
                kana = ""
            avg = (sum(m.conf for m in marks) / len(marks)) if marks else float(t.get("avg_conf") or 0)
            files.append({
                "name": name,
                "duration": t.get("duration") or (wav_duration(self.audio_path(t))),
                "text": text,
                "kana": kana,
                "avg_conf": round(avg, 3),
                "marks": [mk.to_row() for mk in marks],
            })
            t["status"] = "analyzed" if marks else t.get("status", "imported")
            t["avg_conf"] = round(avg, 3)
        data = {"library": self.name, "src_dir": self.audio_dir, "files": files}
        path = os.path.join(self.path, "project.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.save()
        return path

    def build_bank(self) -> dict:
        self.write_project()
        r = B.build_bank(self.path, self.audio_dir)
        B.write_report(self.path)
        return r

    def export_clips(self, dst: str = "clips", pad_head: float | None = None,
                     pad_tail: float | None = None, shift: float = 0.0,
                     all_takes: bool = True, progress=None) -> dict:
        s = self.settings
        return E.export_clips(
            self.path, self.audio_dir, dst=dst,
            pad_head=s["pad_head"] if pad_head is None else pad_head,
            pad_tail=s["pad_tail"] if pad_tail is None else pad_tail,
            shift=shift, all_takes=all_takes, progress=progress,
        )

    def make_plan(self, lyrics_path: str) -> dict:
        P.make_plan(self.path, lyrics_path)
        summary = os.path.join(self.path, "plan", "_summary.txt")
        txt = ""
        if os.path.exists(summary):
            txt = open(summary, encoding="utf-8").read()
        return {"dir": os.path.join(self.path, "plan"), "summary": txt}

    def render(self, lyrics_path: str, dst: str = "render", head: float = 0.008,
               tail: float = 0.015, crossfade_ms: float = 8.0, target_db: float = -18.0,
               slot: float | None = None, allow_sub: bool = True) -> dict:
        return A.assemble(self.path, self.audio_dir, lyrics_path, dst=dst, head=head,
                          tail=tail, crossfade_ms=crossfade_ms, target_db=target_db,
                          slot=slot, allow_sub=allow_sub)

    # ---------------------------------------------------------------- 編集
    def remove_track(self, name: str, delete_audio: bool = True) -> None:
        m = self.load()
        t = self.track(name)
        m["tracks"] = [x for x in m["tracks"] if x.get("name") != name]
        if t and delete_audio:
            ap = self.audio_path(t)
            if ap and os.path.isfile(ap) and os.path.commonpath(
                    [os.path.abspath(ap), os.path.abspath(self.path)]) == os.path.abspath(self.path):
                try:
                    os.remove(ap)
                except OSError:
                    pass
        for p in (self.marks_path(name),
                  os.path.join(self.path, "labels", f"{safe_name(name)}.txt")):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self.save()
        self.write_project()

    def rename(self, new_name: str) -> "Library":
        new_name = safe_name(new_name)
        old_dir = self.path
        target = os.path.join(os.path.dirname(old_dir), new_name)
        if os.path.abspath(target) != os.path.abspath(old_dir):
            if os.path.exists(target):
                raise FileExistsError(f"既にある: {target}")
            os.rename(old_dir, target)
        lib = Library(target)
        lib.load()
        lib.load()["name"] = new_name
        lib.save()
        lib.touch()
        return lib

    def delete(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
        if os.path.abspath(paths.load_settings().get("last_library", "")) == self.path:
            paths.set_setting("last_library", "")
