"""音声 I/O と前処理（外部 ffmpeg に依存しない）。"""

from __future__ import annotations

import os
import wave
from dataclasses import dataclass

import numpy as np

try:
    import soundfile as sf
except Exception:                      # pragma: no cover
    sf = None

TARGET_SR = 16000


@dataclass
class Audio:
    data: np.ndarray          # float32, mono, [-1, 1]
    sr: int

    @property
    def duration(self) -> float:
        return len(self.data) / self.sr


def read_audio(path: str) -> Audio:
    """任意の wav を float32 モノラルで読む。soundfile が無ければ wave モジュールで読む。"""
    if sf is not None:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        data = data.mean(axis=1)
        return Audio(np.ascontiguousarray(data, dtype=np.float32), int(sr))

    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / float(1 << 23)
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    return Audio(x.astype(np.float32), int(sr))


def write_wav(path: str, data: np.ndarray, sr: int, subtype: str = "PCM_16") -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if sf is not None:
        sf.write(path, data, sr, subtype=subtype)
        return
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x.astype(np.float32)
    try:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(sr_in, sr_out)
        return resample_poly(x, sr_out // g, sr_in // g).astype(np.float32)
    except Exception:
        # 線形補間フォールバック
        n_out = int(round(len(x) * sr_out / sr_in))
        t_in = np.arange(len(x), dtype=np.float64)
        t_out = np.linspace(0, len(x) - 1, n_out)
        return np.interp(t_out, t_in, x).astype(np.float32)


def frame_db(x: np.ndarray, sr: int, win_ms: float = 10.0) -> tuple[np.ndarray, float]:
    """10ms 窓の RMS(dB) を返す。無音判定に使う。"""
    hop = max(1, int(sr * win_ms / 1000.0))
    n = (len(x) - hop) // hop + 1
    if n <= 0:
        return np.zeros(0, dtype=np.float32), hop
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, hop), strides=(x.strides[0] * hop, x.strides[0]), writeable=False
    )
    rms = np.sqrt(np.maximum((frames.astype(np.float64) ** 2).mean(axis=1), 1e-12))
    return (20 * np.log10(rms)).astype(np.float32), hop


def peak_envelope(x: np.ndarray, sr: int, points: int = 1200) -> list[float]:
    """波形描画用の min/max 包絡。"""
    if len(x) == 0:
        return []
    step = max(1, len(x) // points)
    n = len(x) // step
    trimmed = x[: n * step].reshape(n, step)
    return np.maximum(np.abs(trimmed).max(axis=1), 1e-6).round(4).tolist()
