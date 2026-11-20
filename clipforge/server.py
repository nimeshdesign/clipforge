"""ClipForge - web UI + API.  Run: ./run.sh  then open http://localhost:8800"""
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import usage, generate, config, pipeline, live, store, seo, youtube, poster, thumbnails, autopost, ai, discover, performance

WEB = Path(__file__).parent / "web"
app = FastAPI(title="ClipForge")


def _setup_log():
    """data/server.log keeps errors (incl. crashes in background threads such as YouTube sign-in or auto-post),
    so they are not lost when the console window is minimised or closed."""
    import logging, logging.handlers, sys
    h = logging.handlers.RotatingFileHandler(config.DATA / "server.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log = logging.getLogger("clipforge")
    log.setLevel(logging.INFO)
    log.addHandler(h)
    for name in ("uvicorn.error",):
        logging.getLogger(name).addHandler(h)
    threading.excepthook = lambda a: log.error("thread %s crashed", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    sys.excepthook = lambda *e: log.critical("server crashed", exc_info=e)
    log.info("ClipForge starting")


_setup_log()


class JobIn(BaseModel):
    url: str
    mode: str | None = None
    auto_upload: bool = False


class LiveIn(BaseModel):
    url: str
    name: str = ""


class ClipEdit(BaseModel):
    title: str
    description: str
    tags: list[str]


@app.on_event("startup")
def _start():
    pipeline.start()
    live.start()
    autopost.start()
    discover.start()
    performance.start()


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/state")
def state():
    cfg = config.load()
    return {"jobs": [{k: v for k, v in j.items() if k != "opts"} for j in list(pipeline.JOBS.values())[-30:]][::-1],
            "live": [{**s, "state": live.status().get(s["url"], "idle")} for s in cfg["live"].get("streams", [])]
                    + [{**s, "auto": True, "state": live.status().get(s["url"], "idle")} for s in discover.live_streams()],
            "clips": store.all_clips(),
            "youtube": {"configured": youtube.configured(), "authed": youtube.authed(), "channel": youtube.channel_name(), "privacy": cfg["youtube"]["privacy"]},
            "autopost": {**autopost.status(), "enabled": cfg["autopost"]["enabled"], "max": cfg["autopost"]["max_per_window"], "hours": cfg["autopost"]["window_hours"]},
            "discover": {**discover.status(), "enabled": bool(discover.cfg().get("enabled")), "channels": [c["name"] for c in discover.cfg().get("channels") or []]},
            "keys": {"gemini": bool(ai.key())},
            "budget": usage.summary(),
            "mode": cfg["clip"]["mode"], "folder": str(config.clips_dir())}


@app.post("/api/jobs")
def add_job(b: JobIn):
    return pipeline.new_job("url", b.url.strip(), mode=b.mode, auto_upload=b.auto_upload)["id"]


@app.post("/api/live")
def add_live(b: LiveIn):
    cfg = config.load()
    cfg["live"].setdefault("streams", []).append({"url": b.url.strip(), "name": b.name, "enabled": True})
    config.save(cfg)
    return "ok"


@app.post("/api/live/agent")
def live_agent(url: str, enabled: bool):
    cfg = config.load()
    for s in cfg["live"].get("streams", []):
        if s["url"] == url:
            s["star_agent"] = enabled
    config.save(cfg)
    return "ok"


@app.get("/api/usage")
def get_usage():
    return usage.summary()


@app.delete("/api/live")
def del_live(url: str):
    cfg = config.load()
    cfg["live"]["streams"] = [s for s in cfg["live"].get("streams", []) if s["url"] != url]
    config.save(cfg)
    return "ok"


@app.post("/api/settings/{key}")
def setting(key: str, value: str):
    cfg = config.load()
    if key == "mode" and value in ("shorts", "landscape"):
        cfg["clip"]["mode"] = value
    elif key == "privacy" and value in ("private", "unlisted", "public"):
        cfg["youtube"]["privacy"] = value
    elif key == "auto_upload":
        cfg["youtube"]["auto_upload"] = value == "true"
    else:
        raise HTTPException(400)
    config.save(cfg)
    return "ok"


@app.put("/api/clips/{cid}")
def edit(cid: str, b: ClipEdit):
    m = store.load(cid)
    m.update(title=b.title[:100], description=b.description, tags=b.tags)
    store.save(cid, m)
    return m


@app.post("/api/clips/{cid}/seo")
def regen(cid: str):
    m = store.load(cid)
    m.update(seo.generate({"transcript": m.get("transcript", ""), "duration": m["duration"], "source": m["source"]}))
    store.save(cid, m)
    return m


@app.post("/api/clips/{cid}/upload")
def upload(cid: str):
    if not youtube.authed():
        raise HTTPException(400, "Connect YouTube first")
    try:
        poster.post_async(cid)
    except Exception as e:
        raise HTTPException(409, str(e))
    return "started"


@app.post("/api/clips/{cid}/generate")
def gen(cid: str):
    try:
        return generate.generate_all(cid)
    except Exception as e:
        raise HTTPException(500, str(e))


class ThumbIn(BaseModel):
    prompt: str | None = None


@app.post("/api/clips/{cid}/thumbnail")
def thumb(cid: str, b: ThumbIn | None = None):
    try:
        return {"by": thumbnails.make(cid, (b.prompt.strip() or None) if b and b.prompt else None)}
    except Exception as e:
        raise HTTPException(500, str(e))


class PromptIn(BaseModel):
    text: str


@app.get("/api/prompts/thumbnail_director")
def get_director():
    mine = config.DATA / "prompts" / "thumbnail_director.md"
    return {"text": config.prompt("thumbnail_director"), "custom": mine.exists(), "has_placeholder": True}


@app.put("/api/prompts/thumbnail_director")
def put_director(b: PromptIn):
    if "{format_spec}" not in b.text:
        raise HTTPException(400, "Keep the {format_spec} placeholder - it is replaced by the portrait/landscape rules")
    (config.DATA / "prompts" / "thumbnail_director.md").write_text(b.text, encoding="utf-8")
    return "saved"


@app.delete("/api/prompts/thumbnail_director")
def reset_director():
    (config.DATA / "prompts" / "thumbnail_director.md").unlink(missing_ok=True)
    return {"text": config.prompt("thumbnail_director")}


@app.post("/api/clips/{cid}/star")
def star(cid: str, value: bool = True):
    return store.update(cid, star=value, starred_by="user" if value else None)


@app.post("/api/clips/{cid}/approve")
def approve(cid: str, value: bool = True):
    return store.update(cid, approved=value)


@app.post("/api/clips/{cid}/privacy")
def clip_privacy(cid: str, value: str):
    if value not in ("private", "unlisted", "public"):
        raise HTTPException(400)
    return store.update(cid, privacy=value)


@app.post("/api/youtube/auth")
def yt_auth():
    if not youtube.configured():
        raise HTTPException(400, "Put your OAuth client JSON in secrets/client_secret.json first")
    threading.Thread(target=youtube.auth, daemon=True).start()
    return "Google sign-in opened in your browser"


@app.post("/api/youtube/logout")
def yt_logout():
    youtube.logout()
    return "ok"


@app.get("/api/performance")
def perf_state():
    return performance.state()


@app.post("/api/performance/refresh")
def perf_refresh():
    try:
        performance.refresh()
    except Exception as e:
        raise HTTPException(500, f"Could not read YouTube stats: {e}")
    return performance.state()


@app.post("/api/performance/insights")
def perf_insights():
    try:
        performance.insights()
    except Exception as e:
        raise HTTPException(500, f"AI suggestions failed: {e}")
    return performance.state()


@app.post("/api/autopost")
def autopost_toggle(enabled: bool):
    cfg = config.load()
    cfg["autopost"]["enabled"] = enabled
    config.save(cfg)
    return "ok"


@app.delete("/api/clips/{cid}")
def delete(cid: str):
    store.delete(cid)
    return "ok"


app.mount("/media", StaticFiles(directory=str(config.clips_dir())), name="media")
