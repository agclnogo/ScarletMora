"""日本語の読み・仮名正規化・モーラ分割・音素列変換。

モーラは「切り出す単位」であり、音素は「整列させる単位」。
この 2 つを分けて持つことで、CTC 強制整列の結果をモーラ境界に畳める。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 正規化

_KATA = "ァィゥェォヵヶッャュョァ-ヶ"
_HIRA = "ぁぃぅぇぉゕゖっゃゅょぁ-ゖ"


def _kata2hira(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:            # ァ..ヶ
            out.append(chr(o - 0x60))
        elif ch == "ヴ":
            out.append("ゔ")
        elif ch == "ヷ":
            out.append("わ")
        elif ch == "ヺ":
            out.append("を")
        else:
            out.append(ch)
    return "".join(out)


POUSE_CHARS = set("、。，．,.!?！？…‥「」『』（）()・ 　\t\n\r")


def normalize(text: str) -> str:
    """カタカナ→ひらがな。句読点や空白はそのまま残す（あとで pau に変換する）。"""
    return _kata2hira(text.strip())


# ---------------------------------------------------------------- 読み

_sudachi = None


def _tokenizer():
    global _sudachi
    if _sudachi is None:
        from sudachipy import dictionary

        _sudachi = dictionary.Dictionary(dict="core").create()
    return _sudachi


_D = {"0": "ぜろ", "1": "いち", "2": "に", "3": "さん", "4": "よん", "5": "ご",
      "6": "ろく", "7": "なな", "8": "はち", "9": "きゅう"}

# 助詞の「は」以外で「わ」と読む決まり文句（感動詞）
_WA_WORDS = {"こんにちは": "こんにちわ", "こんばんは": "こんばんわ"}


def _read_int(n: int) -> str:
    """0..9999 の整数を日本語読みに。歌詞の「105」みたいな数字用。"""
    if n == 0:
        return "ぜろ"
    out = []
    for val, one, name in ((1000, "せん", "せん"),
                           (100, "ひゃく", "ひゃく"),
                           (10, "じゅう", "じゅう")):
        d = n // val
        n %= val
        if d == 0:
            continue
        if d == 1:
            out.append(name)
        elif d == 3 and val == 100:
            out.append("さんびゃく")
        elif d == 6 and val == 100:
            out.append("ろっぴゃく")
        elif d == 8 and val == 100:
            out.append("はっぴゃく")
        elif d == 3 and val == 1000:
            out.append("さんぜん")
        elif d == 8 and val == 1000:
            out.append("はっせん")
        else:
            out.append(_D[str(d)] + name)
    if n:
        out.append(_D[str(n)])
    return "".join(out)


def numbers_to_kana(text: str) -> str:
    """数字（全角含む）を読みに開く。105 → ひゃくご。"""
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if "0" <= ch <= "9" or "０" <= ch <= "９":
            j = i
            buf = ""
            while j < len(text) and ("0" <= text[j] <= "9" or "０" <= text[j] <= "９"):
                c = text[j]
                buf += chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c
                j += 1
            if len(buf) <= 4:
                out.append(_read_int(int(buf)))
            else:
                out.extend(_D[d] for d in buf)
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def to_kana(text: str) -> str:
    """漢字混じり文をカタカナ読みに変換する（SudachiPy）。

    助詞の「は」は「わ」と読む（私は → わたしわ）。感動詞の「こんにちは」も同じ。
    失敗したら入力をそのまま返す。
    """
    text = numbers_to_kana(text)
    try:
        tk = _tokenizer()
    except Exception:
        return text
    out: list[str] = []
    try:
        for m in tk.tokenize(text):
            surf = m.surface() or ""
            hira = _kata2hira(surf)
            if hira in _WA_WORDS:                 # こんにちは / こんばんは
                out.append(_WA_WORDS[hira])
                continue
            read = m.reading_form() or surf
            pos = m.part_of_speech() or ("",)
            if pos[0] == "助詞" and hira == "は":   # 主題・係助詞の「は」= wa
                read = (read[:-1] + "ワ") if read.endswith("ハ") else "ワ"
            out.append(read)
        return "".join(out)
    except Exception:
        return text


# ---------------------------------------------------------------- 音素表

_V = {"あ": "a", "い": "i", "う": "u", "え": "e", "お": "o"}

# 1 文字かな → 音素列
_KANA = {
    # 母音
    "あ": ["a"], "い": ["i"], "う": ["u"], "え": ["e"], "お": ["o"],
    "ぁ": ["a"], "ぃ": ["i"], "ぅ": ["u"], "ぇ": ["e"], "ぉ": ["o"],
    # k
    "か": ["k", "a"], "き": ["k", "i"], "く": ["k", "u"], "け": ["k", "e"], "こ": ["k", "o"],
    # g
    "が": ["g", "a"], "ぎ": ["g", "i"], "ぐ": ["g", "u"], "げ": ["g", "e"], "ご": ["g", "o"],
    # s
    "さ": ["s", "a"], "し": ["sh", "i"], "す": ["s", "u"], "せ": ["s", "e"], "そ": ["s", "o"],
    # z
    "ざ": ["z", "a"], "じ": ["j", "i"], "ず": ["z", "u"], "ぜ": ["z", "e"], "ぞ": ["z", "o"],
    # t
    "た": ["t", "a"], "ち": ["ch", "i"], "つ": ["ts", "u"], "て": ["t", "e"], "と": ["t", "o"],
    # d
    "だ": ["d", "a"], "ぢ": ["j", "i"], "づ": ["z", "u"], "で": ["d", "e"], "ど": ["d", "o"],
    # n
    "な": ["n", "a"], "に": ["n", "i"], "ぬ": ["n", "u"], "ね": ["n", "e"], "の": ["n", "o"],
    # h
    "は": ["h", "a"], "ひ": ["h", "i"], "ふ": ["f", "u"], "へ": ["h", "e"], "ほ": ["h", "o"],
    # b
    "ば": ["b", "a"], "び": ["b", "i"], "ぶ": ["b", "u"], "べ": ["b", "e"], "ぼ": ["b", "o"],
    # p
    "ぱ": ["p", "a"], "ぴ": ["p", "i"], "ぷ": ["p", "u"], "ぺ": ["p", "e"], "ぽ": ["p", "o"],
    # m
    "ま": ["m", "a"], "み": ["m", "i"], "む": ["m", "u"], "め": ["m", "e"], "も": ["m", "o"],
    # y
    "や": ["y", "a"], "ゆ": ["y", "u"], "よ": ["y", "o"],
    # r
    "ら": ["r", "a"], "り": ["r", "i"], "る": ["r", "u"], "れ": ["r", "e"], "ろ": ["r", "o"],
    # w
    "わ": ["w", "a"], "ゐ": ["i"], "ゑ": ["e"], "を": ["o"], "ん": ["N"],
    # v
    "ゔ": ["v", "u"],
    # 小書き単独（外来語の尻尾など）
    "ゕ": ["k", "a"], "ゖ": ["k", "e"],
}

# 拗音（2 文字で 1 モーラ）
_YOON = {
    "きゃ": ["ky", "a"], "きゅ": ["ky", "u"], "きょ": ["ky", "o"],
    "ぎゃ": ["gy", "a"], "ぎゅ": ["gy", "u"], "ぎょ": ["gy", "o"],
    "しゃ": ["sh", "a"], "しゅ": ["sh", "u"], "しょ": ["sh", "o"],
    "じゃ": ["j", "a"], "じゅ": ["j", "u"], "じょ": ["j", "o"],
    "ちゃ": ["ch", "a"], "ちゅ": ["ch", "u"], "ちょ": ["ch", "o"],
    "ぢゃ": ["j", "a"], "ぢゅ": ["j", "u"], "ぢょ": ["j", "o"],
    "にゃ": ["ny", "a"], "にゅ": ["ny", "u"], "にょ": ["ny", "o"],
    "ひゃ": ["hy", "a"], "ひゅ": ["hy", "u"], "ひょ": ["hy", "o"],
    "びゃ": ["by", "a"], "びゅ": ["by", "u"], "びょ": ["by", "o"],
    "ぴゃ": ["py", "a"], "ぴゅ": ["py", "u"], "ぴょ": ["py", "o"],
    "みゃ": ["my", "a"], "みゅ": ["my", "u"], "みょ": ["my", "o"],
    "りゃ": ["ry", "a"], "りゅ": ["ry", "u"], "りょ": ["ry", "o"],
}

# 小書き母音との融合（2 文字で 1 モーラ）
_SMALLV = {
    "ふぁ": ["f", "a"], "ふぃ": ["f", "i"], "ふぇ": ["f", "e"], "ふぉ": ["f", "o"],
    "てぃ": ["t", "i"], "でぃ": ["d", "i"],
    "とぅ": ["t", "u"], "どぅ": ["d", "u"],
    "うぃ": ["w", "i"], "うぇ": ["w", "e"], "うぉ": ["w", "o"],
    "しぇ": ["sh", "e"], "じぇ": ["j", "e"], "ちぇ": ["ch", "e"],
    "つぁ": ["ts", "a"], "つぃ": ["ts", "i"], "つぇ": ["ts", "e"], "つぉ": ["ts", "o"],
    "ゔぁ": ["v", "a"], "ゔぃ": ["v", "i"], "ゔぇ": ["v", "e"], "ゔぉ": ["v", "o"],
    "いぇ": ["y", "e"], "きぇ": ["ky", "e"], "ぎぇ": ["gy", "e"],
    "すぃ": ["s", "i"], "ずぃ": ["z", "i"],
    "くぁ": ["kw", "a"], "くぃ": ["kw", "i"], "くぇ": ["kw", "e"], "くぉ": ["kw", "o"],
    "ぐぁ": ["gw", "a"], "ぐぃ": ["gw", "i"], "ぐぇ": ["gw", "e"], "ぐぉ": ["gw", "o"],
}

# 融合の土台になりうる末尾文字
_SMALLV_BASE = set("うふてでとどしじちつゔいきぎすずくぐ")
_YOON_BASE = set("きぎしじちぢにひびぴみり")


@dataclass
class Mora:
    text: str                       # 表記（きゃ / っ / ー / ん など）
    phones: list[str] = field(default_factory=list)
    kind: str = "cv"                # v / cv / yoon / n / q / long / pause
    char_pos: int = 0               # 元の読み文字列内の開始位置
    phrase: int = 0                 # 句の番号（句読点で区切る）

    @property
    def key(self) -> str:
        """索引に使うキー。長音は直前のモーラ表記を引き継がないので 'ー' のまま。"""
        return self.text


def mora_split(kana: str, phrases: list[str] | None = None) -> list[Mora]:
    """カタカナ/ひらがな交じり文字列をモーラ列に分割する。

    phrases を渡すと、句ごとの区切り位置として使う（無くても句読点で自動分割）。
    """
    s = normalize(kana)
    out: list[Mora] = []
    phrase_idx = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]

        if ch in POUSE_CHARS:
            i += 1
            # 句読点が来たら句番号を進める（連続する句読点は 1 回だけ）
            if i >= n or s[i] not in POUSE_CHARS:
                if out:
                    phrase_idx += 1
            continue

        if ch == "っ":
            out.append(Mora("っ", ["cl"], "q", i, phrase_idx))
            i += 1
            continue

        if ch == "ー":
            prev_v = None
            for m in reversed(out):
                if m.kind != "pause" and m.phones:
                    prev_v = m.phones[-1]
                    break
            out.append(Mora("ー", [prev_v or "a"], "long", i, phrase_idx))
            i += 1
            continue

        if ch == "ん":
            out.append(Mora("ん", ["N"], "n", i, phrase_idx))
            i += 1
            continue

        # 2 文字モーラ（拗音・小書き母音）
        two = s[i:i + 2]
        if len(two) == 2:
            if two in _YOON and two[0] in _YOON_BASE:
                out.append(Mora(two, list(_YOON[two]), "yoon", i, phrase_idx))
                i += 2
                continue
            if two in _SMALLV and two[0] in _SMALLV_BASE:
                out.append(Mora(two, list(_SMALLV[two]), "yoon", i, phrase_idx))
                i += 2
                continue

        if ch in _KANA:
            phones = list(_KANA[ch])
            kind = "v" if phones[0] in ("a", "i", "u", "e", "o") else "cv"
            out.append(Mora(ch, phones, kind, i, phrase_idx))
            i += 1
            continue

        # 未知文字（記号・英数字など）は捨てるが、位置だけ進める
        i += 1

    return out


def phrase_groups(moras: list[Mora]) -> list[list[Mora]]:
    """モーラ列を句ごとにまとめる。"""
    groups: list[list[Mora]] = []
    cur: list[Mora] = []
    last = None
    for m in moras:
        if last is not None and m.phrase != last:
            if cur:
                groups.append(cur)
            cur = []
        cur.append(m)
        last = m.phrase
    if cur:
        groups.append(cur)
    return groups


VOWELS = set("aiueo")


def vowel_of(mora: Mora) -> str | None:
    if mora.kind == "q":
        return None
    for p in reversed(mora.phones):
        if p in VOWELS:
            return p
    return None


def mora_vowel_map(moras: list[Mora]) -> dict[str, set[str]]:
    """モーラ表記 → 含まれる母音の集合（索引のグルーピング用）。"""
    d: dict[str, set[str]] = {}
    for m in moras:
        v = vowel_of(m)
        if v:
            d.setdefault(m.key, set()).add(v)
    return d
