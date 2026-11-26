"""Find the moments worth clipping.

AI picker (default when a Gemini key is set): the whole video is transcribed once and an LLM reads the timestamped
transcript, picking clips that start on a hook and end on the payoff. Start/end are snapped to sentence boundaries.

Signal fallback (no key, budget reached, or picker.mode: signal) - ideas borrowed from clipping-gta-6 / lxgic-clipper:
  loudness  - peaks above the video's own normal level
  jump      - sudden rise vs the previous seconds (screams, reactions, explosions)
  heatmap   - YouTube "Most replayed" graph, when the source has one
  keywords  - hype / GTA words in the Whisper transcript of each candidate
"""
import subprocess, json, threading, re
import numpy as np
from . import config, ai, usage

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
    lead = int(cc.get("hook_lead_seconds", 4))   # peak lands in the first seconds: Shorts viewers swipe within ~2s
    s = score.copy()
    out = []
    for _ in range(limit * 3):
        t = int(s.argmax())
        if s[t] <= 0:
            break
        start = max(0, t - lead)
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


def transcribe_all(path, n, model_name, log=print, piece=600):
    """Whole-file transcript in absolute seconds, done in 10-minute pieces (bounded memory, progress in the log)."""
    out = []
    for a in range(0, n, piece):
        b = min(n, a + piece)
        if n > piece:
            log(f"Transcribing minute {a // 60}-{b // 60} of {n // 60}…")
        for sg in transcribe(path, a, b, model_name):
            sg["start"] += a
            sg["end"] += a
            for w in sg["words"]:
                w["start"] += a
                w["end"] += a
            out.append(sg)
    return out


def slice_segments(full, start, end):
    """Segments of a full transcript inside [start, end], times relative to start (what render expects)."""
    out = []
    for sg in full:
        if sg["end"] <= start or sg["start"] >= end:
            continue
        words = [dict(w, start=max(0.0, w["start"] - start), end=min(end, w["end"]) - start)
                 for w in sg["words"] if w["start"] >= start - 0.05 and w["start"] < end]
        if sg["words"] and not words:
            continue
        text = " ".join(w["w"] for w in words) if sg["words"] else sg["text"]
        out.append({"start": max(0.0, sg["start"] - start), "end": min(end, sg["end"]) - start, "text": text, "words": words})
    return out


def _boundaries(full):
    """Where a clip may start (sentence starts) and end (sentence ends): segment edges, plus words around . ! ?"""
    starts, ends = set(), set()
    for sg in full:
        starts.add(sg["start"])
        ends.add(sg["end"])
        ws = sg["words"]
        for i, w in enumerate(ws):
            if re.search(r"[.!?]$", w["w"]):
                ends.add(w["end"])
                if i + 1 < len(ws):
                    starts.add(ws[i + 1]["start"])
    return sorted(starts), sorted(ends)


def _near(points, t, tol):
    best = min(points, key=lambda p: abs(p - t), default=None)
    return best if best is not None and abs(best - t) <= tol else t


def snap(start, end, full, n, lo, hi):
    """Snap an AI-picked range to sentence boundaries and force its length into [lo, hi] seconds."""
    starts, ends = _boundaries(full)
    start = _near(starts, start, 3)
    end = _near(ends, end, 3)
    if end - start > hi or end - start < lo:
        ok = [e for e in ends if start + lo <= e <= start + hi - 0.5]
        if end - start > hi:
            end = ok[-1] if ok else start + hi - 0.5
        else:
            end = ok[0] if ok else start + lo
    start = max(0.0, start - 0.15)              # tiny lead-in so the first syllable isn't cut
    end = min(float(n), end + 0.4)              # let the last word / reaction ring out
    if end - start < lo:                        # ran into the end of the video
        start = max(0.0, end - lo)
    return round(start, 2), round(end, 2)


def ai_pick(full, score, n, info, cfg, log=print):
    """One LLM call per ~40k characters of transcript. Returns windows (same shape as the signal path), or None if the AI is unavailable."""
    cc, pk = cfg["clip"], cfg.get("picker") or {}
    model = pk.get("model") or cfg["ai"]["gemini_text_model"]
    want = cc["max_per_source"]
    lines = []
    for sg in full:
        a = int(sg["start"])
        b = max(a + 1, int(np.ceil(sg["end"])))
        loud = int(min(9, score[a:b].max() * 9)) if a < len(score) else 0
        txt = re.sub(r"\s+", " ", sg["text"]).strip()
        if txt:
            lines.append(f"{a}|{loud}|{txt}")
    if not lines:
        return None
    chunks, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) > int(pk.get("max_chars", 40000)):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    chunks.append(cur)
    head = (config.prompt("clip_picker").replace("{title}", (info or {}).get("title") or "unknown")
            .replace("{lo}", str(cc["min_seconds"])).replace("{hi}", str(cc["max_seconds"]))
            .replace("{tgt}", str(cc["target_seconds"])).replace("{n}", str(want)))
    picks = []
    for i, ch in enumerate(chunks, 1):
        log("AI picker reading the transcript" + (f" (part {i}/{len(chunks)})" if len(chunks) > 1 else "") + "…")
        try:
            res = ai.text_json(model, head + "\n".join(ch), tag="picker", think="low")
        except usage.BudgetExceeded as e:
            log(f"AI picker skipped: {e}")
            return None
        except Exception as e:
            log(f"AI picker failed: {e}")
            return None
        picks += [r for r in (res if isinstance(res, list) else []) if isinstance(r, dict)]
    out = []
    for r in sorted(picks, key=lambda r: -float(r.get("score") or 0)):
        try:
            v, s0, e0 = float(r["score"]), float(r["start"]), float(r["end"])
        except Exception:
            continue
        if v < float(pk.get("min_score", 6)) or not 0 <= s0 < n or e0 <= s0:
            continue
        start, end = snap(s0, e0, full, n, cc["min_seconds"], cc["max_seconds"])
        if any(start < w["end"] - 1 and end > w["start"] + 1 for w in out):
            continue                            # overlaps a better pick
        segs = slice_segments(full, start, end)
        text = " ".join(x["text"] for x in segs)
        a = int(start)
        sub = score[a:max(a + 1, int(end))]
        out.append({"start": start, "end": end, "segments": segs, "transcript": text,
                    "peak": a + int(sub.argmax()) if len(sub) else a, "signal": float(sub.max()) if len(sub) else 0.0,
                    "kw": kw_score(text, cfg["keywords"]), "score": round(v / 10, 3), "viral": v,
                    "hook": str(r.get("hook", ""))[:120], "reason": str(r.get("reason", ""))[:120],
                    "title": str(r.get("title") or "").strip()[:100] or None,
                    "overlay": str(r.get("overlay") or "").strip()[:60] or None, "picked_by": "ai"})
        if len(out) >= want:
            break
    log(f"AI picker chose {len(out)} clip(s)")
    return out


def use_ai_picker(cfg):
    return (cfg.get("picker") or {}).get("mode", "ai") == "ai" and bool(ai.key())


def find_clips(path, info, cfg, log=print):
    cc, w = cfg["clip"], cfg["weights"]
    log("Scanning audio for loud / exciting moments…")
    score, n = signal_score(path, info, w)
    if n < cc["min_seconds"]:
        log("Video too short")
        return []
    full = None
    if use_ai_picker(cfg):
        log("Transcribing the whole video for the AI picker…")
        full = transcribe_all(path, n, cfg["asr"]["model"], log)
        if full:
            wins = ai_pick(full, score, n, info, cfg, log)
            if wins is not None:
                return wins
            log("Falling back to loudness-based picking")
        else:
            log("No speech found (or Whisper missing) - using loudness-based picking")
    wins = pick_windows(score, n, cc, cc["max_per_source"] * 2)
    log(f"{len(wins)} candidate moments" + (" - transcribing them" if full is None else ""))
    wk = w["keywords"]
    for win in wins:
        if full is not None:
            win["segments"] = slice_segments(full, win["start"], win["end"])
        else:
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
