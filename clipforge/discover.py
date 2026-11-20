"""Find source videos automatically: watch a list of channels (config `discover.channels`).

Every `check_every_minutes` the newest uploads of each channel are read with yt-dlp (public pages, no API quota).
A video is queued for clipping when its title contains one of `title_must_include` (e.g. 'gta 6', not just 'gta'), it is between `min_minutes`
and `max_minutes` long and not older than `max_age_hours`. Candidates are ranked by views per hour (fastest
growing first) and at most `max_per_day` are queued per day, `max_per_channel_per_day` from any one channel. Every video is considered once (data/discover.json).
When `record_live` is on, each channel's /live page is also recorded by the live recorder while the channel is live.
"""
import datetime, json, logging, subprocess, sys, threading, time
from . import config, pipeline

F = config.DATA / "discover.json"
log = logging.getLogger("clipforge")
_state = {"msg": "off"}
_lock = threading.Lock()


def _load():
    try:
        return json.loads(F.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d):
    F.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def _ytdlp(args, timeout=120):
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    try:
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except ValueError:
        return None


def latest(url, n):
    """Newest regular uploads of a channel (the /videos tab: no Shorts, no live streams)."""
    d = _ytdlp(["--flat-playlist", "-J", "--playlist-end", str(n), url.rstrip("/") + "/videos"])
    return [e for e in (d or {}).get("entries") or [] if e and e.get("id")]


def details(vid):
    return _ytdlp(["-J", "--skip-download", "--no-playlist", f"https://www.youtube.com/watch?v={vid}"], timeout=90)


def cfg():
    return config.load().get("discover") or {}


def live_streams():
    """Channel /live pages for the live recorder (only while discovery and record_live are on)."""
    c = cfg()
    if not c.get("enabled") or not c.get("record_live", True):
        return []
    return [{"url": ch["url"].rstrip("/") + "/live", "name": f"{ch['name']} live", "enabled": True}
            for ch in c.get("channels") or [] if ch.get("enabled", True)]


def check():
    c = cfg()
    if not c.get("enabled"):
        _state["msg"] = "off"
        return
    with _lock:
        seen = _load()
        now = datetime.datetime.now()
        today = now.date().isoformat()
        cap = int(c.get("max_per_day", 4))
        done_today = sum(1 for v in seen.values() if str(v.get("queued", "")).startswith(today))
        if done_today >= cap:
            _state.update(msg=f"daily limit reached ({done_today}/{cap} videos queued today)", queued_today=done_today)
            return
        words = [w.lower() for w in c.get("title_must_include") or []]
        lo, hi = c.get("min_minutes", 3) * 60, c.get("max_minutes", 180) * 60
        max_age = c.get("max_age_hours", 72)
        cands = []
        for ch in c.get("channels") or []:
            if not ch.get("enabled", True):
                continue
            _state["msg"] = f"checking {ch['name']}…"
            for e in latest(ch["url"], c.get("check_latest", 8)):
                vid, title, dur = e["id"], e.get("title") or "", e.get("duration") or 0
                if vid in seen or (words and not any(w in title.lower() for w in words)):
                    continue
                if dur and not lo <= dur <= hi:
                    seen[vid] = {"skipped": "length", "title": title, "channel": ch["name"]}
                    continue
                d = details(vid)
                if not d or d.get("live_status") in ("is_live", "is_upcoming"):
                    continue                                   # live streams are handled by the live recorder
                ts = d.get("timestamp") or (datetime.datetime.strptime(d["upload_date"], "%Y%m%d").timestamp()
                                            if d.get("upload_date") else None)
                age_h = max(1.0, (time.time() - ts) / 3600) if ts else None
                if age_h is None or age_h > max_age:
                    seen[vid] = {"skipped": "too old", "title": title, "channel": ch["name"]}
                    continue
                vph = (d.get("view_count") or 0) / age_h
                if vph < c.get("min_views_per_hour", 0):
                    continue                                   # may grow later: look again next round
                cands.append({"vph": vph, "id": vid, "title": title, "channel": ch["name"],
                              "url": d.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"})
        cands.sort(key=lambda x: -x["vph"])
        per_ch = {}
        for v in seen.values():
            if str(v.get("queued", "")).startswith(today):
                per_ch[v["channel"]] = per_ch.get(v["channel"], 0) + 1
        picked = []
        for x in cands:
            if len(picked) >= cap - done_today:
                break
            if per_ch.get(x["channel"], 0) < int(c.get("max_per_channel_per_day", 2)):
                picked.append(x)
                per_ch[x["channel"]] = per_ch.get(x["channel"], 0) + 1
        for x in picked:
            pipeline.new_job("url", x["url"])
            seen[x["id"]] = {"queued": now.isoformat(timespec="seconds"), "title": x["title"], "channel": x["channel"],
                             "views_per_hour": round(x["vph"])}
            log.info("discover: queued %s - %s (%d views/h)", x["channel"], x["title"], x["vph"])
        _save(seen)
        done_today += len(picked)
        _state.update(msg=f"queued {len(picked)} new video(s); {done_today}/{cap} today" if picked
                      else f"no new GTA videos ({done_today}/{cap} today)", queued_today=done_today,
                      last_check=now.isoformat(timespec="minutes"))


def status():
    s = dict(_state)
    s["recent"] = [dict(v, id=k) for k, v in list(_load().items()) if v.get("queued")][-10:][::-1]
    return s


def loop():
    time.sleep(30)                                             # let the server finish starting first
    while True:
        try:
            check()
        except Exception as e:
            _state["msg"] = f"error: {str(e)[:120]}"
            log.exception("discover error")
        time.sleep(max(5, int(cfg().get("check_every_minutes", 60))) * 60)


def start():
    threading.Thread(target=loop, daemon=True).start()
