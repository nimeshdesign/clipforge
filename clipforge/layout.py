"""Find the streamer's face-cam overlay so it can be cut out and shown in its own panel.
Samples frames, runs OpenCV face detection, keeps small faces (a cam overlay, not in-game characters) that sit
at the same spot in several frames, and expands them to a cam box. Cached per source in <source>.layout.json
(edit that file to correct the box: {"cam": [x, y, w, h]} in source pixels, or {"cam": null} for no cam)."""
import json, subprocess, tempfile
from pathlib import Path
import numpy as np
from . import config


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    j = json.loads(r.stdout)
    return int(j["streams"][0]["width"]), int(j["streams"][0]["height"]), float(j["format"]["duration"])


def detect_cam(path, n=10):
    import cv2
    W, H, dur = probe(path)
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    tmp = Path(tempfile.mkdtemp())
    boxes = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        f = tmp / f"{i}.jpg"
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=960:-2", str(f)])
        img = cv2.imread(str(f))
        if img is None:
            continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sc = W / img.shape[1]
        for (x, y, w, h) in casc.detectMultiScale(g, 1.1, 6, minSize=(30, 30)):
            if h * sc < 0.30 * H:                       # webcam faces are small; ignore close-ups
                boxes.append([x * sc, y * sc, w * sc, h * sc])
    if len(boxes) < 3:
        return None
    b = np.array(boxes)
    cx, cy = b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2
    mx, my = np.median(cx), np.median(cy)
    keep = b[(abs(cx - mx) < 0.06 * W) & (abs(cy - my) < 0.08 * H)]   # same place in many frames = overlay
    if len(keep) < max(3, n // 3):
        return None
    fx, fy, fw, fh = np.median(keep, axis=0)
    bw = fw * 2.0
    bh = min(fh * 2.5, H * 0.45)
    x = fx + fw / 2 - bw / 2
    y = fy + fh / 2 - bh * 0.5
    x = min(max(0, x), W - bw)
    y = min(max(0, y), H - bh)
    return [int(x) // 2 * 2, int(y) // 2 * 2, int(bw) // 2 * 2, int(bh) // 2 * 2]


def get(path):
    """Layout dict {"cam": [x,y,w,h] | None, "size": [W,H]} cached next to the source file."""
    p = Path(path)
    f = p.with_suffix(".layout.json")
    W, H, _ = probe(p)
    if f.exists():
        d = json.loads(f.read_text())
    else:
        try:
            cam = detect_cam(p)
        except Exception as e:
            print("cam detection failed:", e)
            cam = None
        d = {"cam": cam}
        f.write_text(json.dumps(d))
    d["size"] = [W, H]
    return d
