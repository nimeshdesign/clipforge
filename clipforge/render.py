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


def write_ass(segments, path, vertical=True, margin_v=None):
    """Word-accurate captions: each Whisper word start/end drives its own highlight, so text changes exactly
    when the word is spoken (no interpolation across a segment)."""
    W, H, fs = (1080, 1920, 78) if vertical else (1920, 1080, 58)
    mv = margin_v if margin_v else (480 if vertical else 90)           # stay above the Shorts UI / description overlay
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: D,DejaVu Sans,{fs},{WHITE},{WHITE},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,2,2,60,60,{mv},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    gs = groups(segments)
    lines = []
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


def render(src, start, end, out, mode="shorts", segments=None, captions=True, layout=None):
    out = Path(out)
    vertical = mode == "shorts"
    cam = (layout or {}).get("cam")
    W, H = (layout or {}).get("size", (1920, 1080))
    ass = None
    if captions and segments and any(s.get("words") for s in segments):
        ass = out.with_suffix(".ass")
        # captions sit on the lower part of the game panel (clear of the Shorts UI)
        write_ass(segments, ass, vertical, margin_v=1920 - GAME_H + 40 if cam else 480)
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
