"""Auto-posting rules:
  * at most `max_per_window` posts per `window_hours` (default 2 per 4h), and `min_gap_minutes` between posts
  * clips you approved in the UI go first (best score first)
  * if nothing was posted for `idle_hours` and the system is online, the best remaining clip is posted anyway
"""
import datetime, threading, time
from . import config, store, youtube, poster

_state = {"msg": "off"}


def rank(c):
    ai = c.get("ai") or {}
    return (ai.get("viral_score", 0) / 10) * 0.5 + c.get("score", 0) * 0.5 + (0.5 if c.get("star") else 0)   # star clips first


def candidates():
    out = []
    for c in store.all_clips():
        if c.get("status") == "posted" or (c.get("post") or {}).get("state") in ("uploading", "preparing"):
            continue
        ai = c.get("ai") or {}
        if ai.get("relevant") is False or ai.get("safe") is False:
            continue
        out.append(c)
    return sorted(out, key=rank, reverse=True)


def status():
    return dict(_state)


def tick():
    cfg = config.load()["autopost"]
    if not cfg["enabled"]:
        _state["msg"] = "off"
        return
    if not youtube.authed():
        _state["msg"] = "connect YouTube first"
        return
    recent = youtube.posts_since(cfg["window_hours"])
    last = youtube.last_post()
    now = datetime.datetime.now()
    _state.update(msg="on", posted_in_window=len(recent), limit=cfg["max_per_window"])
    if len(recent) >= cfg["max_per_window"]:
        _state["msg"] = "limit reached for this window"
        return
    if last and now - last < datetime.timedelta(minutes=cfg["min_gap_minutes"]):
        _state["msg"] = "waiting between posts"
        return
    cands = candidates()
    approved = [c for c in cands if c.get("approved")]
    idle = last is None or now - last > datetime.timedelta(hours=cfg["idle_hours"])
    pick = approved[0] if approved else (cands[0] if idle and cands else None)
    if not pick:
        _state["msg"] = "on - nothing to post"
        return
    _state["msg"] = f"posting {pick['id']}"
    poster.post(pick["id"], auto=True)


def loop():
    while True:
        try:
            tick()
        except Exception as e:
            _state["msg"] = f"error: {str(e)[:120]}"
        time.sleep(60)


def start():
    threading.Thread(target=loop, daemon=True).start()
