"""Thumbnail for a clip, in two Gemini steps:
 1. an art-director prompt (prompts/thumbnail_director.md, editable in the UI) + 3 frames + the clip's caption -> the model writes the image brief
 2. the image model (Nano Banana) renders it from that brief + the same 3 frames.
Gemini stamps a small visible mark in the bottom-right corner: the brief keeps that corner empty and we crop it away
(keep the top-left `corner_crop` fraction, scale back up). The streamer's face cam is cut out of the reference frames."""
import subprocess, tempfile, time
from pathlib import Path
from . import config, store, ai

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SPEC = {
    "portrait": dict(w=1080, h=1920, aspect="9:16", ref="720:1280",
                     fmt="Aspect ratio: PORTRAIT 9:16 (vertical, for YouTube Shorts), 1080 x 1920. Keep all text inside the top 60% of the image and away from the right edge (Shorts buttons cover the bottom and right)."),
    "landscape": dict(w=1920, h=1080, aspect="16:9", ref="1280:720",
                      fmt="Aspect ratio: 16:9 landscape, 1920 x 1080 (minimum 1280 x 720)."),
}
RULES = ("Hard rules: {fmt} The bottom-right corner (last 12% of width and height) must be completely empty - plain continuation of the "
         "background, no text, logos, icons or details there. No watermarks, borders or channel logos. Do not make a collage of the "
         "reference frames. Keep the game's recognizable characters, vehicles and locations faithful to the references; do not invent events.")


def _frames(meta, cid, spec, orient):
    """Three reference frames (before / at / after the peak), game only (no face cam), in the thumbnail orientation."""
    src = meta.get("source_file")
    d = Path(tempfile.mkdtemp())
    out = []
    if src and Path(src).exists():
        peak = meta.get("peak", meta["start"] + meta["duration"] / 2)
        times = [meta["start"] + meta["duration"] * 0.2, peak, meta["start"] + meta["duration"] * 0.8]
        if orient == "portrait":
            vf = f"crop=floor(ih*9/16/2)*2:ih:(iw-floor(ih*9/16/2)*2)/2:0,scale={spec['ref']}"
        else:
            vf = f"scale={spec['ref']}:force_original_aspect_ratio=increase,crop={spec['ref']}"
            try:
                from . import layout
                lay = layout.get(src)
                if lay.get("cam"):
                    W, H = lay["size"]; x, y, w, h = lay["cam"]
                    x0, x1 = (0, x) if x + w / 2 > W / 2 else (x + w, W)
                    cw = (x1 - x0) // 2 * 2; ch = min(H, int(cw * 9 / 16)) // 2 * 2
                    vf = f"crop={cw}:{ch}:{x0}:{(H - ch) // 2},scale={spec['ref']}"
            except Exception as e:
                print("cam crop skipped:", e)
        inp = src
    else:                                       # source gone: use the rendered clip's game panel
        clip = str(store.folder(cid) / "clip.mp4")
        times = [meta["duration"] * f for f in (0.2, 0.5, 0.8)]
        vf = ("crop=1080:1180:0:0,crop=664:1180:208:0," if meta.get("mode") == "shorts" else "") + f"scale={spec['ref']}:force_original_aspect_ratio=increase,crop={spec['ref']}"
        inp = clip
    for i, t in enumerate(times):
        f = d / f"{i}.jpg"
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", str(max(0, t)), "-i", inp, "-frames:v", "1", "-vf", vf, str(f)], check=True)
        out.append(f)
    return out


def write_brief(meta, frames, spec):
    """Step 1: the text model plays art director and returns the image brief (+ optional headline)."""
    cfg = config.load()
    role = config.prompt("thumbnail_director").replace("{format_spec}", spec["fmt"])
    ask = (f"{role}\n\n---\nCLIP CONTEXT\nVideo title: {meta['title']}\nSource video: {meta['source'].get('title', '')}\n"
           f"Caption (what is said / happens): {meta.get('transcript', '')[:900] or '(no speech)'}\n"
           "Screenshots 1, 2, 3 are attached in order.\n\n"
           'Return ONLY JSON: {"text": "<headline in ALL CAPS, or empty string if none>", "image_prompt": "<the complete brief>"}')
    j = ai.text_json(cfg["ai"]["gemini_text_model"], ask, [f.read_bytes() for f in frames], tag="thumb_brief")
    return j["image_prompt"].strip(), (j.get("text") or "").strip().upper()


def _finish(raw_path, dest, crop, spec):
    vf = f"crop=iw*{crop}:ih*{crop}:0:0,scale={spec['w']}:{spec['h']}" if crop < 1 else f"scale={spec['w']}:{spec['h']}"
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", str(raw_path), "-vf", vf, "-q:v", "2", str(dest)], check=True)


def make(cid, prompt=None):
    cfg = config.load()["thumbnail"]
    orient = cfg.get("orientation", "portrait")
    spec = SPEC[orient]
    meta = store.load(cid)
    dest = store.folder(cid) / "thumbnail.jpg"
    frames = _frames(meta, cid, spec, orient)
    text = (meta.get("thumb_text") or "GTA 6").upper()
    used = "frame"
    if cfg["provider"] != "template" and ai.key():
        try:
            if not prompt:
                prompt, t2 = write_brief(meta, frames, spec)
                text = t2
            raw = Path(tempfile.mkdtemp()) / "g.png"
            raw.write_bytes(ai.image(cfg["model"], f"{prompt}\n\n{RULES.format(fmt=spec['fmt'])}",
                                     [f.read_bytes() for f in frames], spec["aspect"]))
            _finish(raw, dest, cfg["corner_crop"], spec)
            used = "gemini"
        except Exception as e:
            print("gemini thumbnail failed, using frame:", e)
    if used == "frame":
        base = Path(tempfile.mkdtemp()) / "b.jpg"
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", str(frames[1]), "-vf", f"scale={spec['w']}:{spec['h']}", str(base)], check=True)
        tf = Path(tempfile.mkdtemp()) / "t.txt"
        wds = (text or "GTA 6")[:28].split()
        half = (len(wds) + 1) // 2
        tf.write_text(" ".join(wds[:half]) + "\n" + " ".join(wds[half:]))
        pos = "y=170" if orient == "portrait" else "y=h-text_h-80"
        vf = (f"drawtext=fontfile={FONT}:textfile={tf}:fontsize={130 if orient == 'portrait' else 110}:fontcolor=white:borderw=10:"
              f"bordercolor=black:line_spacing=10:x=(w-text_w)/2:{pos},eq=saturation=1.3:contrast=1.08")
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", str(base), "-vf", vf, "-q:v", "2", str(dest)], check=True)
    store.update(cid, thumbnail=True, thumbnail_by=used, thumb_v=int(time.time()), thumb_text=text,
                 thumb_prompt=prompt if used == "gemini" else meta.get("thumb_prompt"))
    return used
