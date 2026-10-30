"""切点表（marks）のデータ構造と CSV 入出力。"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field, asdict

FIELDS = [
    "idx", "mora", "phones", "start", "end", "dur",
    "on", "vowel_center", "conf", "peak_db", "dip", "flags", "phrase",
]


@dataclass
class Mark:
    idx: int
    mora: str
    phones: str
    start: float
    end: float
    conf: float = 0.0
    on: float = -1.0              # 子音が終わる時刻（母音の立ち上がり）
    vowel_center: float = -1.0
    peak_db: float = -120.0
    dip: float = 0.0              # 境界のエネルギー落ち込み量(dB)。大きいほど切れ目が明瞭
    flags: str = ""
    phrase: int = 0

    @property
    def dur(self) -> float:
        return self.end - self.start

    def to_row(self) -> dict:
        row = asdict(self)
        row["dur"] = round(self.dur, 3)
        for k in ("start", "end", "on", "vowel_center"):
            row[k] = round(row[k], 3)
        row["conf"] = round(row["conf"], 3)
        row["peak_db"] = round(row["peak_db"], 1)
        row["dip"] = round(row["dip"], 1)
        return row

    def interval(self) -> tuple[float, float]:
        return float(self.start), float(self.end)


def write_marks(path: str, marks: list[Mark]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for m in marks:
            w.writerow(m.to_row())


def read_marks(path: str) -> list[Mark]:
    out: list[Mark] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out.append(Mark(
                idx=int(row["idx"]),
                mora=row["mora"],
                phones=row.get("phones", ""),
                start=float(row["start"]),
                end=float(row["end"]),
                conf=float(row.get("conf") or 0),
                on=float(row.get("on") or -1),
                vowel_center=float(row.get("vowel_center") or -1),
                peak_db=float(row.get("peak_db") or -120),
                dip=float(row.get("dip") or 0),
                flags=row.get("flags", ""),
                phrase=int(row.get("phrase") or 0),
            ))
    return out


def write_audacity_labels(path: str, marks: list[Mark], min_dur: float = 0.01) -> None:
    """Audacity のラベルトラック（タブ区切り・BOM なし）。

    秒は 6 桁固定。BOM があると Audacity が 1 行目を読み飛ばすので付けない。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        for m in marks:
            a, b = m.interval()
            if b - a < min_dur:
                b = a + min_dur
            f.write(f"{a:.6f}\t{b:.6f}\t{m.mora}\n")
