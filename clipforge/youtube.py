"""YouTube Data API v3 upload (videos.insert, resumable) + OAuth.
Setup: Google Cloud console -> enable "YouTube Data API v3" -> OAuth client ID (Desktop app)
-> download JSON to secrets/client_secret.json -> run `python -m clipforge.youtube auth`."""
import json, datetime
from . import config, store

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
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
    c = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not c.valid and c.refresh_token:
        c.refresh(Request())
        TOKEN.write_text(c.to_json())
    return c


def auth():
    from google_auth_oauthlib.flow import InstalledAppFlow
    c = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES).run_local_server(port=8765, open_browser=True)
    TOKEN.write_text(c.to_json())
    return channel()


def _yt():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds(), cache_discovery=False)


def channel():
    r = _yt().channels().list(part="snippet", mine=True).execute()
    name = (r.get("items") or [{}])[0].get("snippet", {}).get("title")
    (config.SECRETS / "channel.txt").write_text(name or "")
    return name


def channel_name():
    f = config.SECRETS / "channel.txt"
    return f.read_text() if f.exists() else ""


def logout():
    TOKEN.unlink(missing_ok=True)


def _times():
    try:
        return json.loads(QUOTA.read_text()).get("times", [])
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
        status.update(privacyStatus="private", publishAt=publish_at)
    body = {"snippet": {"title": meta["title"][:100], "description": meta["description"][:4900],
                        "tags": meta["tags"], "categoryId": y["category_id"]}, "status": status}
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
    QUOTA.write_text(json.dumps({"times": ts[-200:]}))
    return store.update(cid, youtube_id=resp["id"], youtube_url=f"https://youtu.be/{resp['id']}",
                        uploaded_at=ts[-1], status="posted", thumb_error=thumb_err)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["auth"]:
        print("Authorised as:", auth())
    elif sys.argv[1:2] == ["upload"]:
        print(upload(sys.argv[2])["youtube_url"])
    else:
        print("usage: python -m clipforge.youtube auth | upload <clip_id>")
