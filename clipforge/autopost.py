"""Auto-posting rules:
  * only between `post_hours` in `timezone` (default 10:00-22:00 US Eastern), so posts land while the audience is awake
  * at most `max_per_window` posts per `window_hours` (default 2 per 4h), and `min_gap_minutes` between posts
  * clips you approved in the UI go first (best score first)
  * then, with `auto_pick`, AI-picked clips rated at least `min_viral` - no approval needed
  * if nothing was posted for `idle_hours` and the system is online, the best remaining clip is posted anyway
  * with `schedule.enabled`, the upload is private and YouTube publishes it at the next `schedule` slot
    (lets the PC upload in its own daytime while videos go public in the audience's morning)
  * `schedule.ab_test` alternates scheduled and direct (public now) uploads; Performance compares them
  * variety: at most `max_per_source` clips per source video (`max_per_source_day` per 24h), and a different
    creator than the last post when possible
"""
import datetime, threading, time
from zoneinfo import ZoneInfo
from . import config, store, youtube, poster

_state = {"msg": "off"}


def rank(c):
    ai = c.get("ai") or {}
    return (ai.get("viral_score", 0) / 10) * 0.5 + c.get("score", 0) * 0.5 + (0.5 if c.get("star") else 0)   # star clips first


def candidates():
    out = []
    for c in store.all_clips():
        p = c.get("post") or {}
        if c.get("status") == "posted" or p.get("state") in ("uploading", "preparing"):
            continue
        if p.get("state") == "error" and time.time() - p.get("at", 0) < 30 * 60:   # failed recently: retry in 30 min
            continue
        ai = c.get("ai") or {}
        if ai.get("relevant") is False or ai.get("safe") is False:
            continue
        out.append(c)
    return sorted(out, key=rank, reverse=True)


def _src_key(c):
    """One key per source video. Live chunks share the channel's /live URL, so a stream counts per day."""
    s = c.get("source") or {}
    key = (s.get("url") or c.get("source_file") or c["id"]).strip()
    if (s.get("channel") or "").strip().lower() == "live":
        key += "#" + (c.get("created") or "")[:10]
    return key


def _creator(c):
    s = c.get("source") or {}
    return (c.get("stream") or s.get("channel") or "").strip().lower().removesuffix(" live").removesuffix(" gta 6")


def diversify(cands, cfg):
    """Variety rules, so the channel doesn't look repetitive to YouTube:
    * at most `max_per_source` clips from one source video ever, and `max_per_source_day` in 24h
    * prefer a different creator than the last post (falls back to the best clip if every candidate shares it)."""
    posted = [c for c in store.all_clips() if c.get("status") == "posted"]
    day_ago = datetime.datetime.now() - datetime.timedelta(hours=24)
    total, today = {}, {}
    for c in posted:
        k = _src_key(c)
        total[k] = total.get(k, 0) + 1
        try:
            if datetime.datetime.fromisoformat(c.get("uploaded_at") or "") > day_ago:
                today[k] = today.get(k, 0) + 1
        except ValueError:
            pass
    ok = [c for c in cands if total.get(_src_key(c), 0) < cfg.get("max_per_source", 2)
          and today.get(_src_key(c), 0) < cfg.get("max_per_source_day", 1)]
    last = max(posted, key=lambda c: c.get("uploaded_at") or "", default=None)
    if last and cfg.get("vary_creator", True):
        other = [c for c in ok if _creator(c) != _creator(last)]
        ok = other + [c for c in ok if _creator(c) == _creator(last)]   # stable: best other-creator clip first
    return ok


def ab_method(cfg):
    """A/B test: alternate 'scheduled' (US-morning publishAt) and 'direct' (public right away) uploads."""
    posted = [c for c in store.all_clips() if c.get("ab")]
    last = max(posted, key=lambda c: c.get("uploaded_at") or "", default=None)
    return "direct" if last and last["ab"] == "scheduled" else "scheduled"


def status():
    return dict(_state)


def next_publish_slot(cfg, now=None):
    """With `schedule.enabled`, uploads go up as private videos that YouTube makes public at the next free slot:
    every `gap_minutes` between `schedule.hours` in `schedule.timezone` (e.g. US morning), at least `lead_minutes`
    from now and never closer than `gap_minutes` to another scheduled clip. None = no free slot in `max_days_ahead`."""
    s = cfg.get("schedule") or {}
    tz = ZoneInfo(s.get("timezone", "America/New_York"))
    a, b = int(s["hours"][0]), int(s["hours"][1])
    gap = datetime.timedelta(minutes=s.get("gap_minutes", 40))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    earliest = now + datetime.timedelta(minutes=s.get("lead_minutes", 30))
    taken = [datetime.datetime.fromisoformat(c["publish_at"].replace("Z", "+00:00"))
             for c in store.all_clips() if c.get("publish_at")]
    taken = [t for t in taken if t > now - gap]
    day = earliest.astimezone(tz).date()
    for _ in range(int(s.get("max_days_ahead", 3)) + 1):
        t = datetime.datetime.combine(day, datetime.time(a), tz)
        end = datetime.datetime.combine(day, datetime.time(b), tz)
        while t < end:
            if t >= earliest and all(abs(t - x) >= gap for x in taken):
                return t
            t += gap
        day += datetime.timedelta(days=1)
    return None


def tick():
    cfg = config.load()["autopost"]
    try:
        youtube.post_pending_comments()
    except Exception as e:
        import logging; logging.getLogger("clipforge").warning("first comment failed: %s", e)
    if not cfg["enabled"]:
        _state["msg"] = "off"
        return
    if not youtube.authed():
        _state["msg"] = "connect YouTube first"
        return
    hours = cfg.get("post_hours")
    if hours:
        tz = cfg.get("timezone", "America/New_York")
        h = datetime.datetime.now(ZoneInfo(tz)).hour
        a, b = int(hours[0]), int(hours[1])
        if not (a <= h < b if a <= b else h >= a or h < b):
            _state["msg"] = f"outside posting hours ({a}:00-{b}:00 {tz.split('/')[-1].replace('_', ' ')} time)"
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
    day_max = config.load()["youtube"]["max_uploads_per_day"]
    if len(youtube.posts_since(24)) >= day_max:
        _state["msg"] = f"daily limit reached ({day_max} in 24h)"
        return
    cands = diversify(candidates(), cfg)
    approved = [c for c in cands if c.get("approved")]
    idle = last is None or now - last > datetime.timedelta(hours=cfg["idle_hours"])
    good = [c for c in cands if (c.get("viral") or 0) >= cfg.get("min_viral", 7)] if cfg.get("auto_pick") else []
    pick = approved[0] if approved else good[0] if good else (cands[0] if idle and cands else None)
    if not pick:
        _state["msg"] = "on - nothing to post"
        return
    publish_at, sched = None, cfg.get("schedule") or {}
    method = ab_method(cfg) if sched.get("enabled") and sched.get("ab_test") else None
    if sched.get("enabled") and method != "direct":
        publish_at = next_publish_slot(cfg)
        if not publish_at:
            _state["msg"] = "schedule full - every publish slot is taken"
            return
    _state["msg"] = f"posting {pick['id']}" + (f" (goes public {publish_at.strftime('%b %d %H:%M')} {publish_at.tzname()})" if publish_at else "") \
        + (f" [A/B: {method}]" if method else "")
    poster.post(pick["id"], auto=True, publish_at=publish_at)
    if method:
        store.update(pick["id"], ab=method)


def loop():
    while True:
        try:
            tick()
        except Exception as e:
            _state["msg"] = f"error: {str(e)[:120]}"
            import logging; logging.getLogger("clipforge").exception("auto-post error")
        time.sleep(60)


def start():
    threading.Thread(target=loop, daemon=True).start()
