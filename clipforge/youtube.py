"""YouTube Data API v3 upload (videos.insert, resumable) + OAuth.
Setup: Google Cloud console -> enable "YouTube Data API v3" -> OAuth client ID (Desktop app)
-> download JSON to secrets/client_secret.json -> run `python -m clipforge.youtube auth`."""
import json, datetime
from . import config, store

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"   # watch time, % viewed, traffic sources
EDIT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"   # edit posted videos (e.g. add creator credit)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly", ANALYTICS_SCOPE,
          EDIT_SCOPE]
CLIENT = config.SECRETS / "client_secret.json"
TOKEN = config.SECRETS / "token.json"
QUOTA = config.DATA / "uploads.json"


def configured():
    return CLIENT.exists()


def authed():
    return TOKEN.exists()


def creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    c = Credentials.from_authorized_user_file(str(TOKEN))   # the scopes actually granted (older logins lack analytics)
    if not c.valid and c.refresh_token:
        c.refresh(Request())
        TOKEN.write_text(c.to_json(), encoding="utf-8")
    return c


import threading
_auth_lock = threading.Lock()


def auth_in_progress():
    return _auth_lock.locked()


def start_auth():
    """For the web UI: start a sign-in in the background unless one is already open. Returns False if one is open.
    The lock is taken here (not in the thread), so two quick clicks can't both start a sign-in."""
    if not _auth_lock.acquire(blocking=False):
        return False
    threading.Thread(target=auth, kwargs={"locked": True}, daemon=True).start()
    return True


def auth(locked=False):
    """Google sign-in in the browser. One at a time; an abandoned sign-in gives up after 10 minutes."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not locked and not _auth_lock.acquire(blocking=False):
        raise RuntimeError("A Google sign-in is already open")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
        c = flow.run_local_server(port=8765, open_browser=True, timeout_seconds=600)
        if not c:
            raise RuntimeError("Sign-in was not completed within 10 minutes")
        TOKEN.write_text(c.to_json(), encoding="utf-8")
        return channel()
    finally:
        _auth_lock.release()


def _yt():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds(), cache_discovery=False)


def channel():
    r = _yt().channels().list(part="snippet", mine=True).execute()
    name = (r.get("items") or [{}])[0].get("snippet", {}).get("title")
    (config.SECRETS / "channel.txt").write_text(name or "", encoding="utf-8")
    return name


def has_scope(scope):
    """True when the saved login was granted `scope` (logins made before a scope was added lack it)."""
    try:
        return scope in (json.loads(TOKEN.read_text(encoding="utf-8")).get("scopes") or [])
    except Exception:
        return False


def has_analytics():
    return has_scope(ANALYTICS_SCOPE)


def video_stats(ids):
    """Public counters per video id (YouTube Data API, 1 quota unit per 50 videos). Missing ids = deleted/private."""
    out = {}
    for i in range(0, len(ids), 50):
        r = _yt().videos().list(part="statistics,status,snippet", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0)), "privacy": it["status"].get("privacyStatus"),
                             "published": it["snippet"].get("publishedAt")}
    return out


def video_analytics(ids, start):
    """Per-video watch metrics from the YouTube Analytics API (data lags 1-3 days). Returns ({id: metrics}, traffic sources)."""
    from googleapiclient.discovery import build
    ya = build("youtubeAnalytics", "v2", credentials=creds(), cache_discovery=False)
    end = datetime.date.today().isoformat()
    m = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,shares,subscribersGained"
    per = {}
    for i in range(0, len(ids), 200):
        r = ya.reports().query(ids="channel==MINE", startDate=start, endDate=end, metrics=m, dimensions="video",
                               filters="video==" + ",".join(ids[i:i + 200])).execute()
        cols = [h["name"] for h in r.get("columnHeaders", [])]
        for row in r.get("rows") or []:
            d = dict(zip(cols, row))
            per[d.pop("video")] = d
    r = ya.reports().query(ids="channel==MINE", startDate=start, endDate=end, metrics="views",
                           dimensions="insightTrafficSourceType", sort="-views").execute()
    traffic = {row[0]: row[1] for row in r.get("rows") or []}
    return per, traffic


def channel_name():
    f = config.SECRETS / "channel.txt"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def logout():
    TOKEN.unlink(missing_ok=True)


def _times():
    try:
        return json.loads(QUOTA.read_text(encoding="utf-8")).get("times", [])
    except Exception:
        return []


def posts_since(hours):
    cut = datetime.datetime.now() - datetime.timedelta(hours=hours)
    return [t for t in (datetime.datetime.fromisoformat(x) for x in _times()) if t > cut]


def last_post():
    ts = _times()
    return datetime.datetime.fromisoformat(ts[-1]) if ts else None


def upload(cid, publish_at=None, progress=None):
    from googleapiclient.http import MediaFileUpload
    cfg = config.load()
    y = cfg["youtube"]
    if len(posts_since(24)) >= y["max_uploads_per_day"]:
        raise RuntimeError(f"Daily upload limit reached ({y['max_uploads_per_day']}). Raise youtube.max_uploads_per_day (API quota ~1,600 units/upload).")
    meta = store.load(cid)
    status = {"privacyStatus": meta.get("privacy") or y["privacy"], "selfDeclaredMadeForKids": y["made_for_kids"]}
    if publish_at:  # scheduled publishing requires private + RFC3339 time
        if isinstance(publish_at, datetime.datetime):
            publish_at = publish_at.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        status.update(privacyStatus="private", publishAt=publish_at)
    lang = y.get("language", "en-US")      # tells YouTube which audience/language to recommend the video to
    from . import seo
    desc = seo.with_credit(cfg, meta.get("description", ""), meta.get("source") or {})   # every upload credits the creator
    body = {"snippet": {"title": meta["title"][:100], "description": desc[:4900],
                        "tags": meta["tags"], "categoryId": y["category_id"],
                        "defaultLanguage": lang, "defaultAudioLanguage": lang}, "status": status}
    media = MediaFileUpload(str(store.folder(cid) / "clip.mp4"), chunksize=4 * 1024 * 1024, resumable=True)
    yt = _yt()
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st and progress:
            progress(int(st.progress() * 100))
    thumb_err = None
    th = store.folder(cid) / "thumbnail.jpg"
    if th.exists():
        try:
            yt.thumbnails().set(videoId=resp["id"], media_body=MediaFileUpload(str(th))).execute()
        except Exception as e:  # e.g. channel not phone-verified yet
            thumb_err = str(e)[:200]
    ts = _times() + [datetime.datetime.now().isoformat(timespec="seconds")]
    QUOTA.write_text(json.dumps({"times": ts[-200:]}), encoding="utf-8")
    return store.update(cid, youtube_id=resp["id"], youtube_url=f"https://youtu.be/{resp['id']}",
                        uploaded_at=ts[-1], publish_at=publish_at, status="posted", thumb_error=thumb_err,
                        description=desc)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["auth"]:
        print("Authorised as:", auth())
    elif sys.argv[1:2] == ["upload"]:
        print(upload(sys.argv[2])["youtube_url"])
    else:
        print("usage: python -m clipforge.youtube auth | upload <clip_id>")
