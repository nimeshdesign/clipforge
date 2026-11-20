"""Job queue: URL / file -> download -> find moments -> render clips -> SEO -> (optional) upload."""
import json, queue, re, subprocess, sys, threading, time, uuid, datetime
from pathlib import Path
from . import config, agent, layout as layout_mod, analyze, render, seo, store, youtube, ai

JOBS = {}          # id -> job dict (newest last)
Q = queue.Queue()
_lock = threading.Lock()


def new_job(kind, target, **opts):
    j = {"id": uuid.uuid4().hex[:8], "kind": kind, "target": target, "status": "queued", "log": [],
         "clips": [], "created": time.time(), "opts": opts}
    JOBS[j["id"]] = j
    Q.put(j["id"])
    return j


def _log(j, msg):
    j["log"].append(msg)
    j["log"] = j["log"][-40:]


def download(url, j):
    _log(j, "Downloading with yt-dlp…")
    out = config.SOURCES / "%(id)s.%(ext)s"
    cmd = [sys.executable, "-m", "yt_dlp", "--ffmpeg-location", config.FFMPEG, "--js-runtimes", "node", "--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "--write-info-json", "-o", str(out), "--print", "after_move:filepath", url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("yt-dlp failed: " + r.stderr[-300:])
    path = Path(r.stdout.strip().splitlines()[-1])
    info = {}
    ij = path.with_suffix(".info.json")
    if ij.exists():
        info = json.loads(ij.read_text(encoding="utf-8"))
    return path, info


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "clip"


def process(j):
    cfg = config.load()
    j["status"] = "running"
    kind, target = j["kind"], j["target"]
    if kind == "url":
        path, info = download(target, j)
    else:  # local file or live chunk
        path = Path(target)
        info = j["opts"].get("info", {})
        if j["opts"].get("live") and (store.is_processed(path.resolve()) or any(c.get("source_file") == str(path.resolve()) for c in store.all_clips())):
            _log(j, "Chunk already processed - skipping")
            store.mark_processed(path.resolve())
            j["status"] = "done"
            return
    src = {"title": info.get("title") or j["opts"].get("title") or path.stem, "channel": info.get("uploader") or j["opts"].get("channel", ""),
           "url": info.get("webpage_url") or j["opts"].get("url", "")}
    cfg_run = json.loads(json.dumps(cfg))
    if j["opts"].get("mode"):
        cfg_run["clip"]["mode"] = j["opts"]["mode"]
    lay = layout_mod.get(path) if cfg_run["clip"]["mode"] == "shorts" else None
    _log(j, f"Face cam: {lay['cam'] if lay and lay['cam'] else 'not found'}")
    wins = analyze.find_clips(path, info, cfg_run, lambda m: _log(j, m))
    ai_picked = bool(wins) and all(w.get("picked_by") == "ai" for w in wins)
    if j["opts"].get("agent") and ai_picked:         # the AI picker already rated these: reuse it, no second call
        for w in wins:
            w["hype"], w["label"] = w["viral"], w["reason"]
            w["star"] = w["viral"] >= cfg_run["agent"]["min_hype"]
        _log(j, f"AI picker rated {sum(1 for w in wins if w['star'])} hype moment(s)")
        if cfg_run["agent"]["only_starred"]:
            wins = [w for w in wins if w["star"]]
    elif j["opts"].get("agent"):                     # humour agent: one cheap call rates this chunk's best moments
        n = agent.rate(wins, cfg_run)
        if n is None:
            _log(j, "Humour agent unavailable (budget/API) - keeping top 2 unrated moments")
            wins = wins[:2]
        else:
            _log(j, f"Humour agent: {n} hype moment(s)")
            if cfg_run["agent"]["only_starred"]:
                wins = [w for w in wins if w.get("star")]
    _log(j, f"{len(wins)} clips selected - rendering")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    for i, w in enumerate(wins, 1):
        cid = f"{stamp}_{slug(src['title'])}_{i:02d}"
        d = store.folder(cid)
        d.mkdir(parents=True, exist_ok=True)
        render.render(path, w["start"], w["end"], d / "clip.mp4", cfg_run["clip"]["mode"],
                      w["segments"], cfg_run["clip"]["captions"], lay)
        dur = round(w["end"] - w["start"], 1)
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", str(dur * 0.4), "-i", str(d / "clip.mp4"), "-frames:v", "1", "-vf", "scale=360:-2", str(d / "clip.jpg")])
        title = w.get("title") or seo.generate_title({"transcript": w["transcript"]}, cfg_run)
        meta = {"title": title, "description": "", "tags": [], "thumb_text": "",
                "picked_by": w.get("picked_by", "signal"), "viral": w.get("viral"), "hook": w.get("hook"), "viral_reason": w.get("reason")}
        meta.update(id=cid, source=src, source_file=str(path.resolve()), peak=w["peak"], ai=None, stream=(j["opts"].get("title") if j["opts"].get("live") else None), stream_url=(j["opts"].get("url") if j["opts"].get("live") else None), status="new", approved=False, generated=False, star=bool(w.get("star")), hype=w.get("hype"), hype_label=w.get("label"), transcript=w["transcript"], start=w["start"], end=w["end"], score=w["score"], signal=round(w.get("signal",0),3), kw=round(w.get("kw",0),3), duration=dur,
                    mode=cfg_run["clip"]["mode"], created=datetime.datetime.now().isoformat(timespec="seconds"))
        store.save(cid, meta)
        j["clips"].append(cid)
        _log(j, f"Clip {i}/{len(wins)}: {meta['title']}")
        if cfg["youtube"]["auto_upload"] or j["opts"].get("auto_upload"):
            try:
                youtube.upload(cid)
                _log(j, f"Uploaded {cid}")
            except Exception as e:
                _log(j, f"Upload failed: {e}")
    if kind != "url":
        store.mark_processed(Path(path).resolve())
    j["status"] = "done"


def worker():
    while True:
        jid = Q.get()
        j = JOBS[jid]
        try:
            process(j)
        except Exception as e:
            j["status"] = "error"
            _log(j, f"ERROR: {e}")


def start():
    for _ in range(max(1, int(config.load().get("workers", 3)))):
        threading.Thread(target=worker, daemon=True).start()
