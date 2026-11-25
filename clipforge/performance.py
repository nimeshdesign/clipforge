"""Performance of posted videos: YouTube numbers per clip, patterns, and AI suggestions for what to change.

refresh()  - views/likes/comments (YouTube Data API, ~1 quota unit per 50 videos) for every posted clip, plus watch time,
             % viewed, subscribers and traffic sources when the login has the YouTube Analytics permission.
             Keeps a view-count history so growth can be shown. Runs every `performance.refresh_minutes`.
insights() - one free-tier Gemini call: what works, what doesn't, and concrete changes (incl. config settings).
"""
import datetime, json, logging, threading, time
from zoneinfo import ZoneInfo
from . import ai, config, store, youtube

CACHE = config.DATA / "performance.json"
HIST = config.DATA / "perf_history.json"
INS = config.DATA / "perf_insights.json"
log = logging.getLogger("clipforge")
_lock = threading.Lock()


def _read(f, default):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(f, d):
    f.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def _settings():
    return config.load().get("performance") or {}


def _source(c):
    s = c.get("source") or {}
    return (c.get("stream") or s.get("channel") or s.get("title") or "?").strip()


def _length_bucket(d):
    return "under 20s" if d < 20 else "20-35s" if d < 35 else "35-55s" if d < 55 else "55s+"


def _hour_bucket(h):
    a = h // 3 * 3
    return f"{a:02d}-{a + 3:02d}h"


def refresh():
    with _lock:
        clips = [c for c in store.all_clips() if c.get("youtube_id")]
        if not clips or not youtube.authed():
            data = {"rows": [], "fetched_at": datetime.datetime.now().isoformat(timespec="minutes"),
                    "analytics": "not connected" if not youtube.authed() else "no posted videos"}
            _write(CACHE, data)
            return data
        ids = [c["youtube_id"] for c in clips]
        stats = youtube.video_stats(ids)
        per, traffic, a_status = {}, {}, "missing permission"
        if youtube.has_analytics():
            try:
                first = min(c.get("uploaded_at", "")[:10] for c in clips) or datetime.date.today().isoformat()
                per, traffic = youtube.video_analytics(ids, first)
                a_status = "ok"
            except Exception as e:                       # e.g. YouTube Analytics API not enabled in the Cloud project
                a_status = "error: " + str(e)[:200]
                log.warning("analytics unavailable: %s", e)
        ap = config.load().get("autopost", {})   # report times in the audience's zone, not the uploading PC's
        tz = ZoneInfo((ap.get("schedule") or {}).get("timezone") or ap.get("audience_timezone") or "America/New_York")
        now = datetime.datetime.now(datetime.timezone.utc)
        hist = _read(HIST, {})
        rows = []
        for c in clips:
            vid = c["youtube_id"]
            s = stats.get(vid)
            if s is None:
                rows.append({"cid": c["id"], "id": vid, "title": c["title"], "removed": True})
                continue
            posted = datetime.datetime.fromisoformat((c.get("publish_at") or c.get("uploaded_at") or s["published"]).replace("Z", "+00:00"))
            posted = posted.astimezone(datetime.timezone.utc) if posted.tzinfo else posted.astimezone().astimezone(datetime.timezone.utc)
            hours = max(1.0, (now - posted).total_seconds() / 3600)
            a = per.get(vid, {})
            h = hist.setdefault(vid, [])
            if not h or h[-1][1] != s["views"] or (now.timestamp() - h[-1][0]) > 6 * 3600:
                h.append([int(now.timestamp()), s["views"]])
                del h[:-200]
            rows.append({
                "cid": c["id"], "id": vid, "url": c.get("youtube_url"), "title": c["title"], "source": _source(c),
                "posted": posted.astimezone(tz).isoformat(timespec="minutes"), "post_hour": posted.astimezone(tz).hour,
                "age_hours": round(hours, 1), "duration": round(c.get("duration") or 0, 1), "ai_score": c.get("viral"),
                "auto": bool((c.get("post") or {}).get("auto")), "privacy": s["privacy"],
                "method": "scheduled" if c.get("publish_at") else "direct", "ab": c.get("ab"),
                "views": s["views"], "likes": s["likes"], "comments": s["comments"],
                "views_per_hour": round(s["views"] / hours, 1),
                "like_rate": round(100 * s["likes"] / s["views"], 2) if s["views"] else 0,
                "avg_view_pct": a.get("averageViewPercentage"), "avg_view_sec": a.get("averageViewDuration"),
                "watch_minutes": a.get("estimatedMinutesWatched"), "shares": a.get("shares"),
                "subs_gained": a.get("subscribersGained"), "hook": c.get("hook"),
            })
        _write(HIST, hist)
        live = [r for r in rows if not r.get("removed")]
        data = {"rows": rows, "traffic": traffic, "analytics": a_status,
                "fetched_at": datetime.datetime.now().isoformat(timespec="minutes"),
                "totals": {"videos": len(live), "views": sum(r["views"] for r in live), "likes": sum(r["likes"] for r in live),
                           "comments": sum(r["comments"] for r in live),
                           "subs_gained": sum(r["subs_gained"] or 0 for r in live) if a_status == "ok" else None,
                           "avg_view_pct": _avg([r["avg_view_pct"] for r in live]),
                           "removed": sum(1 for r in rows if r.get("removed"))},
                "groups": {
                    "Source channel": _group(live, lambda r: r["source"]),
                    "Clip length": _group(live, lambda r: _length_bucket(r["duration"])),
                    f"Posting time ({tz.key.split('/')[-1].replace('_', ' ')})": _group(live, lambda r: _hour_bucket(r["post_hour"])),
                    "Publish method": _group(live, lambda r: r.get("method") or "direct"),
                    "A/B test (scheduled vs direct)": _group([r for r in live if r.get("ab")], lambda r: r["ab"]),
                    "AI score": _group(live, lambda r: f"{int(r['ai_score'])}/10" if r["ai_score"] is not None else "not rated"),
                }}
        _write(CACHE, data)
        return data


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _group(rows, key):
    g = {}
    for r in rows:
        g.setdefault(key(r), []).append(r)
    out = [{"name": k, "videos": len(v), "avg_views": round(sum(r["views"] for r in v) / len(v)),
            "avg_views_per_hour": _avg([r["views_per_hour"] for r in v]), "avg_view_pct": _avg([r["avg_view_pct"] for r in v])}
           for k, v in g.items()]
    return sorted(out, key=lambda x: -x["avg_views"])


def insights():
    """Ask Gemini (free tier) what to change, based on the latest numbers. Cached in data/perf_insights.json."""
    data = _read(CACHE, None) or refresh()
    live = [r for r in data.get("rows", []) if not r.get("removed")]
    if not live:
        raise RuntimeError("No posted videos with stats yet")
    cfg = config.load()
    ap, cc, dc = cfg.get("autopost", {}), cfg.get("clip", {}), cfg.get("discover", {})
    lines = "\n".join(f"{r['id']}|{r['title'][:70]}|{r['source']}|{r['duration']}s|AI {r['ai_score']}|posted {r['posted'][5:16]}|"
                      f"{r['age_hours']}h old|{r['views']} views|{r['views_per_hour']}/h|{r['likes']} likes|{r['comments']} comments|"
                      f"viewed {r['avg_view_pct'] if r['avg_view_pct'] is not None else '?'}%|hook: {(r.get('hook') or '')[:60]}"
                      for r in sorted(live, key=lambda r: -r["views"])[:60])
    groups = json.dumps(data.get("groups", {}), ensure_ascii=False)
    prompt = f"""You are a YouTube Shorts growth analyst for a GTA 6 clips channel aimed at US viewers.
Posted videos ({len(live)}; id|title|source|length|AI pick score|posted (US Eastern)|age|views|views per hour|likes|comments|avg % viewed|opening line):
{lines}

Averages by group: {groups}
Traffic sources (views): {json.dumps(data.get('traffic') or {})}
Current settings: clip length {cc.get('min_seconds')}-{cc.get('max_seconds')}s (target {cc.get('target_seconds')}s); only clips with AI score >= {ap.get('min_viral')} are auto-posted; posting hours {ap.get('post_hours')} {ap.get('timezone')}; up to {ap.get('max_per_window')} posts per {ap.get('window_hours')}h; source channels {[c.get('name') for c in dc.get('channels') or []]}.

Find what drives views and retention here. Be specific to this data, and say plainly when there are too few videos (or videos too new) to be sure.
Reply ONLY with JSON:
{{"summary": "2-3 sentences",
 "working": ["pattern that performs well, with numbers"],
 "not_working": ["pattern that underperforms, with numbers"],
 "actions": [{{"change": "one concrete change", "why": "evidence from the data", "setting": "config key to change, e.g. autopost.post_hours, or '' if none", "value": "suggested value or ''"}}],
 "videos": [{{"id": "video id", "note": "why this one did well or badly, and what to copy or avoid"}}],
 "confidence": "low|medium|high"}}"""
    res = ai.text_json(cfg["ai"]["gemini_text_model"], prompt, tag="insights")
    res["generated_at"] = datetime.datetime.now().isoformat(timespec="minutes")
    res["based_on"] = len(live)
    _write(INS, res)
    return res


def state():
    return {"data": _read(CACHE, None), "insights": _read(INS, None), "analytics_permission": youtube.has_analytics(),
            "history": _read(HIST, {})}


def loop():
    time.sleep(60)
    while True:
        try:
            if youtube.authed():
                refresh()
        except Exception:
            log.exception("performance refresh failed")
        time.sleep(max(15, int(_settings().get("refresh_minutes", 120))) * 60)


def start():
    threading.Thread(target=loop, daemon=True).start()
