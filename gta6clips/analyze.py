"""Find the moments worth clipping.

Signals (ideas borrowed from clipping-gta-6 / lxgic-clipper):
  loudness  - peaks above the video's own normal level
  jump      - sudden rise vs the previous seconds (screams, reactions, explosions)
  heatmap   - YouTube "Most replayed" graph, when the source has one
  keywords  - hype / GTA words in the Whisper transcript of each candidate
"""
import subprocess, json, threading
import numpy as np
from . import config

SR = 16000
_model = None
_wlock = threading.Lock()   # one Whisper run at a time (CPU heavy); everything else runs in parallel


def audio_db(path):
    """Per-second loudness (dB) of the file's audio."""
    raw = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = len(a) // SR
    if n == 0:
        return np.zeros(0)
    a = a[: n * SR].reshape(n, SR)
    return 20 * np.log10(np.sqrt((a ** 2).mean(axis=1)) + 1e-6)


def norm(x):
    if len(x) == 0:
        return x
    lo, hi = np.percentile(x, 5), np.percentile(x, 99)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1)


def heat_series(info, n):
    h = np.zeros(n)
    for seg in (info or {}).get("heatmap") or []:
        a, b = int(seg["start_time"]), int(np.ceil(seg["end_time"]))
        h[a:min(b, n)] = seg["value"]
    return h


def signal_score(path, info, w):
    db = audio_db(path)
    n = len(db)
    if n == 0:
        return np.zeros(0), 0
    loud = norm(db - np.median(db))
    jump = np.zeros(n)
    for t in range(5, n):
        jump[t] = db[t] - db[t - 5:t].mean()
    jump = norm(np.clip(jump, 0, None))
    heat = norm(heat_series(info, n))
    has_heat = heat.any()
    wl, wj, wh = w["loudness"], w["jump"], w["heatmap"] if has_heat else 0
    s = (wl * loud + wj * jump + wh * heat) / (wl + wj + wh)
    k = np.ones(5) / 5
    return np.convolve(s, k, mode="same"), n


def pick_windows(score, n, cc, limit):
    lo, tgt, hi = cc["min_seconds"], cc["target_seconds"], cc["max_seconds"]
    s = score.copy()
    out = []
    for _ in range(limit * 3):
        t = int(s.argmax())
        if s[t] <= 0:
            break
        start = max(0, t - int(tgt * 0.6))
        end = min(n, start + tgt)
        start = max(0, end - tgt)
        if end - start >= lo:
            out.append({"start": start, "end": end, "peak": t, "signal": float(score[t])})
        s[max(0, t - tgt):t + tgt] = 0
    return out


def transcribe(path, start, end, model_name):
    """Whisper transcript of one window -> list of {start,end,text} (times relative to the window)."""
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    raw = subprocess.run([config.FFMPEG, "-v", "error", "-ss", str(start), "-t", str(end - start), "-i", str(path),
                          "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768  # array input avoids PyAV decoding
    with _wlock:
        if _model is None:
            _model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segs, _ = _model.transcribe(audio, vad_filter=True, language="en", word_timestamps=True)
        segs = list(segs)
    return [{"start": s.start, "end": s.end, "text": s.text.strip(),
             "words": [{"w": w.word.strip(), "start": w.start, "end": w.end} for w in (s.words or [])]} for s in segs]


def kw_score(text, kws):
    t = text.lower()
    return min(1.0, sum(1 for k in kws if k in t) / 4)


def find_clips(path, info, cfg, log=print):
    cc, w = cfg["clip"], cfg["weights"]
    log("Scanning audio for loud / exciting moments…")
    score, n = signal_score(path, info, w)
    if n < cc["min_seconds"]:
        log("Video too short")
        return []
    wins = pick_windows(score, n, cc, cc["max_per_source"] * 2)
    log(f"{len(wins)} candidate moments - transcribing them")
    wk = w["keywords"]
    for i, win in enumerate(wins, 1):
        try:
            win["segments"] = transcribe(path, win["start"], win["end"], cfg["asr"]["model"])
        except Exception as e:  # whisper missing / failed: still usable by signal alone
            log(f"transcribe skipped: {e}")
            win["segments"] = []
        win["transcript"] = " ".join(s["text"] for s in win["segments"])
        win["kw"] = kw_score(win["transcript"], cfg["keywords"])
        win["score"] = round((win["signal"] + wk * win["kw"]) / (1 + wk), 3)
    wins = [x for x in wins if x["score"] >= cc["min_score"]]
    wins.sort(key=lambda x: -x["score"])
    return wins[: cc["max_per_source"]]
