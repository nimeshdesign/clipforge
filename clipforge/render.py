import subprocess, re
from pathlib import Path
from .config import FFMPEG

# ASS colours are BGR: white text, yellow for the word being spoken right now
WHITE, YELLOW = "&H00FFFFFF&", "&H0000E5FF&"


def _ts(t):
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def groups(segments, size=3, gap=0.6):
    """Flatten Whisper word timings into short on-screen phrases (<=3 words, split on pauses / sentence ends)."""
    words = [w for s in segments for w in s.get("words", []) if w["w"]]
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= size or w["start"] - cur[-1]["end"] > gap or re.search(r"[.!?]$", cur[-1]["w"])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def _clean(t):
    return re.sub(r"\s+", " ", re.sub(r"[{}\\]", "", t or "")).strip()


def hook_text(title, max_words=6):
    """Fallback on-screen hook from a title: drop the 'GTA 6' boilerplate and emoji, keep it short and loud."""
    t = re.sub(r"(?i)\s*(\(|\b)(in |on )?(gta ?6|gta ?vi)\)?\s*[:!?-]?\s*", " ", title or "")
    t = re.sub(r"[^\w\s'$,!?.&%-]", "", t).strip(" -:,.")
    w = t.split()
    return " ".join(w[:max_words]).upper() + ("..." if len(w) > max_words else "")


def write_ass(segments, path, vertical=True, margin_v=None, hook=None, cta=None, duration=None, hook_seconds=2.5, cta_seconds=2.0):
    """Word-accurate captions: each Whisper word start/end drives its own highlight, so text changes exactly
    when the word is spoken (no interpolation across a segment).
    hook: big title over the first `hook_seconds` (why keep watching); cta: follow line over the last `cta_seconds`."""
    W, H, fs = (1080, 1920, 78) if vertical else (1920, 1080, 58)
    mv = margin_v if margin_v else (480 if vertical else 90)           # stay above the Shorts UI / description overlay
    top = 190 if vertical else 60                                      # below the Shorts top bar
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: D,DejaVu Sans,{fs},{WHITE},{WHITE},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,2,2,60,60,{mv},1\n"
            f"Style: H,DejaVu Sans,{int(fs * 1.15)},{YELLOW},{YELLOW},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,8,70,70,{top},1\n"
            f"Style: C,DejaVu Sans,{int(fs * 0.9)},{WHITE},{WHITE},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,6,3,8,70,70,{top},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    lines = []
    hook_end = 0.0
    if hook and _clean(hook):
        hook_end = min(hook_seconds, duration or hook_seconds)
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(hook_end)},H,,0,0,0,,{{\\q0\\fad(120,250)}}{_clean(hook).upper()}")
    if cta and _clean(cta) and duration and duration - cta_seconds > hook_end + 1:
        lines.append(f"Dialogue: 1,{_ts(duration - cta_seconds)},{_ts(duration)},C,,0,0,0,,{{\\q0\\fad(200,0)}}{_clean(cta).upper()}")
    gs = groups(segments or [])
    for gi, g in enumerate(gs):
        texts = [re.sub(r"[{}\\]", "", w["w"]).upper() for w in g]
        nxt = gs[gi + 1][0]["start"] if gi + 1 < len(gs) else 1e9
        for i, w in enumerate(g):
            start = w["start"]
            spoken_end = min(w["end"], start + max(0.35, 0.12 * len(texts[i])))  # Whisper can stretch a word over silence
            end = g[i + 1]["start"] if i + 1 < len(g) else min(spoken_end + 0.2, nxt)
            end = max(end, start + 0.08)
            txt = " ".join(f"{{\\c{YELLOW}}}{t}{{\\c{WHITE}}}" if j == i else t for j, t in enumerate(texts))
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},D,,0,0,0,,{txt}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


GAME_H, CAM_H = 1180, 740      # shorts: game panel on top, face-cam panel below (sum 1920)


def _even(v):
    return int(v) // 2 * 2


def add_overlays(clip, duration, hook=None, cta=None, hook_seconds=2.5, cta_seconds=2.0):
    """Burn the hook / follow line onto an already-rendered clip (made before overlays existed).
    Keeps the original as clip_raw.mp4; re-encodes video only, audio is copied."""
    clip = Path(clip)
    raw = clip.with_name("clip_raw.mp4")
    if not raw.exists():
        clip.replace(raw)
    ass = clip.with_name("overlay.ass")
    write_ass([], ass, True, hook=hook, cta=cta, duration=duration, hook_seconds=hook_seconds, cta_seconds=cta_seconds)
    cmd = [FFMPEG, "-y", "-v", "error", "-i", raw.name, "-vf", f"ass={ass.name}", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", clip.name]
    r = subprocess.run(cmd, cwd=clip.parent, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raw.replace(clip)                       # leave the clip as it was
        raise RuntimeError("overlay failed: " + r.stderr[-300:])
    return clip


def render(src, start, end, out, mode="shorts", segments=None, captions=True, layout=None, hook=None, cta=None,
           hook_seconds=2.5, cta_seconds=2.0):
    out = Path(out)
    vertical = mode == "shorts"
    cam = (layout or {}).get("cam")
    W, H = (layout or {}).get("size", (1920, 1080))
    ass = None
    words = captions and segments and any(s.get("words") for s in segments)
    if words or hook or cta:
        ass = out.with_suffix(".ass")
        # captions sit on the lower part of the game panel (clear of the Shorts UI); hook / follow line at the top
        write_ass(segments if words else [], ass, vertical, margin_v=1920 - GAME_H + 40 if cam else 480,
                  hook=hook, cta=cta, duration=end - start, hook_seconds=hook_seconds, cta_seconds=cta_seconds)
    sub = f",ass={ass.name}" if ass else ""
    if vertical and cam:
        # game: punch-in on the centre, filling the top panel (no letterbox, no blur)
        cw = _even(H * 1080 / GAME_H)
        gx = _even((W - cw) / 2)
        cx, cy, cwid, chei = cam
        fc = (f"[0:v]split=3[g][c1][c2];[g]crop={cw}:{H}:{gx}:0,scale=1080:{GAME_H}[game];"
              f"[c1]crop={cwid}:{chei}:{cx}:{cy},scale=1080:{CAM_H}:force_original_aspect_ratio=increase,crop=1080:{CAM_H},boxblur=30:6[cbg];"
              f"[c2]crop={cwid}:{chei}:{cx}:{cy},scale=-2:{CAM_H - 60}[cfg];"
              f"[cbg][cfg]overlay=(W-w)/2:(H-h)/2[cam];[game][cam]vstack{sub}[v]")
    elif vertical:
        # no face cam found: zoomed game on top, blurred copy below
        cw = _even(H * 1080 / 1300)
        gx = _even((W - cw) / 2)
        fc = (f"[0:v]split=2[g][b];[g]crop={cw}:{H}:{gx}:0,scale=1080:1300[game];"
              f"[b]scale=1080:620:force_original_aspect_ratio=increase,crop=1080:620,boxblur=30:6[bot];[game][bot]vstack{sub}[v]")
    else:
        fc = "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2" + sub + "[v]"
    # -ss before -i = same zero point as the Whisper audio slice, so caption times line up with the voice
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", str(start), "-t", str(end - start), "-i", str(Path(src).resolve()),
           "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out.name]
    r = subprocess.run(cmd, cwd=out.parent, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(r.stderr[-400:])
    return out
