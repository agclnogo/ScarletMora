"""CTC 強制整列（Viterbi）。torchaudio に依存せず numpy で実装する。

入力: 音素列（既知のテキストから作る）＋ 音響モデルのフレーム毎ログ事後確率
出力: 音素ごとの [開始フレーム, 終了フレーム) と信頼度
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np

NEG_INF = -1e30


@dataclass
class Span:
    start: int          # フレーム番号（含む）
    end: int            # フレーム番号（含まない）
    conf: float         # 0..1（区間に割り当てられたフレームの平均事後確率）

    @property
    def dur(self) -> int:
        return self.end - self.start


def viterbi_align(logp: np.ndarray, labels: list[int]) -> tuple[list[Span], float]:
    """CTC 強制整列。logp は (T, V) の対数確率。"""
    T, V = logp.shape
    S = len(labels)
    if S == 0:
        return [], float(logp[:, 0].mean())
    if T < S:
        raise ValueError(f"音声が短すぎる: frames={T} phones={S}")

    n_states = 2 * S + 1
    # 状態 → 音素 index（偶数は blank なので -1）
    emit_idx = np.full(n_states, -1, dtype=np.int64)
    emit_idx[1::2] = labels
    is_blank = np.zeros(n_states, dtype=bool)
    is_blank[0::2] = True

    # 同じ音素が連続する場合、2 つ先へのスキップは禁止
    can_skip = np.zeros(n_states, dtype=bool)
    for s in range(2, n_states, 2):
        if emit_idx[s] != -1 and emit_idx[s] != emit_idx[s - 2]:
            can_skip[s] = True

    # 各フレーム・各状態の emission log-prob
    if is_blank.any():
        blank_id = int(np.where(is_blank)[0][0])
        # 実際の blank トークン id は呼び出し側で渡す
    blank_token = getattr(viterbi_align, "_blank_id", 0)
    emis = np.empty((T, n_states), dtype=np.float64)
    emis[:, is_blank] = logp[:, blank_token][:, None]
    emis[:, ~is_blank] = logp[:, emit_idx[~is_blank]]

    dp = np.full((T, n_states), NEG_INF, dtype=np.float64)
    bp = np.zeros((T, n_states), dtype=np.int8)

    dp[0, 0] = emis[0, 0]
    if n_states > 1:
        dp[0, 1] = emis[0, 1]

    for t in range(1, T):
        prev = dp[t - 1]
        best = prev.copy()
        arg = np.zeros(n_states, dtype=np.int8)

        # +1
        cand = np.empty(n_states)
        cand[0] = NEG_INF
        cand[1:] = prev[:-1]
        m = cand > best
        best = np.where(m, cand, best)
        arg = np.where(m, 1, arg)

        # +2（スキップ許可状態のみ）
        if n_states > 2:
            cand2 = np.full(n_states, NEG_INF)
            cand2[2:] = np.where(can_skip[2:], prev[:-2], NEG_INF)
            m2 = cand2 > best
            best = np.where(m2, cand2, best)
            arg = np.where(m2, 2, arg)

        dp[t] = best + emis[t]
        bp[t] = arg

    # 終端は blank か最後の音素
    if dp[T - 1, n_states - 1] >= dp[T - 1, n_states - 2]:
        s = n_states - 1
    else:
        s = n_states - 2
    total = float(dp[T - 1, s])

    path = np.empty(T, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(bp[t, s])

    spans: list[Span] = []
    core = []
    for i in range(S):
        st = 2 * i + 1
        frames = np.where(path == st)[0]
        if len(frames) == 0:
            prev_end = core[-1].end if core else 0
            core.append(Span(prev_end, prev_end + 1, 0.0))
            continue
        a, b = int(frames[0]), int(frames[-1]) + 1
        tok = labels[i]
        lp = logp[a:b, tok]
        # 事後確率 = exp(logp_token - logsumexp)
        mx = logp[a:b].max(axis=1, keepdims=True)
        lse = np.log(np.exp(logp[a:b] - mx).sum(axis=1)) + mx[:, 0]
        post = float(np.exp(lp - lse).mean())
        core.append(Span(a, b, post))

    # このモデルは CTC が「尖った」出力をするので、芯だけだと 1〜3 フレームしかない。
    # 隣り合う芯の間の空白（blank）を中間で折半して、実際の区間まで広げる。
    # ただし伸ばすのは片側 MAX_EXT フレームまで。それ以上は本当の無音なので、
    # 音と音の間に「隙間」として残す（長い休符をモーラに吸わせない）。
    MAX_EXT = 10            # 10 フレーム = 200ms
    spans = [Span(s.start, s.end, s.conf) for s in core]
    for i in range(len(spans) - 1):
        mid = (spans[i].end + spans[i + 1].start) // 2
        if mid > spans[i].end:
            spans[i].end = min(mid, spans[i].end + MAX_EXT)
        if mid < spans[i + 1].start:
            spans[i + 1].start = max(mid, spans[i + 1].start - MAX_EXT)

    return spans, total, path


def _log_softmax(x: np.ndarray) -> np.ndarray:
    m = x.max(axis=-1, keepdims=True)
    return x - m - np.log(np.exp(x - m).sum(axis=-1, keepdims=True))


def greedy_decode(logp: np.ndarray, id2tok: dict[int, str], blank_id: int) -> list[str]:
    ids = logp.argmax(axis=1)
    out: list[str] = []
    prev = -1
    for i in ids:
        i = int(i)
        if i != prev and i != blank_id:
            out.append(id2tok.get(i, "?"))
        prev = i
    return out


class Aligner:
    def __init__(self, model_dir: str, device: str | None = None, hf_endpoint: str = "https://hf-mirror.com"):
        import torch
        from transformers import HubertForCTC

        os.environ.setdefault("HF_ENDPOINT", hf_endpoint)
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = HubertForCTC.from_pretrained(model_dir).to(self.device).eval()
        self.sr = 16000

        vocab_path = os.path.join(model_dir, "vocab.json")
        with open(vocab_path, encoding="utf-8") as f:
            self.tok2id = json.load(f)
        self.id2tok = {int(v): k for k, v in self.tok2id.items()}
        self.blank_id = int(self.tok2id.get("PAD", 0))
        viterbi_align._blank_id = self.blank_id
        self.frame_hop = 0.02   # hubert base @16k: 320 samples / 20ms

    def log_probs(self, wav: np.ndarray, chunk_s: float = 90.0) -> np.ndarray:
        torch = self.torch
        outs = []
        step = int(chunk_s * self.sr)
        for a in range(0, len(wav), step):
            seg = wav[a:a + step]
            if len(seg) < 400:
                continue
            x = torch.from_numpy(seg).float().unsqueeze(0).to(self.device)
            with torch.inference_mode():
                logits = self.model(x).logits[0].float().cpu().numpy()
            outs.append(logits)
        if not outs:
            return np.zeros((0, len(self.tok2id)), dtype=np.float32)
        return _log_softmax(np.concatenate(outs, axis=0).astype(np.float64))

    def phones_to_ids(self, phones: list[str]) -> list[int]:
        ids: list[int] = []
        for p in phones:
            if p not in self.tok2id:
                continue
            ids.append(int(self.tok2id[p]))
        return ids
