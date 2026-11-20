"""Post one clip: thumbnail (optional) -> YouTube upload, with status the UI can poll. One post at a time."""
import threading, time
from . import config, store, youtube, thumbnails, generate

_lock = threading.Lock()


def post(cid, auto=False):
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Another upload is in progress")
    try:
        m = store.load(cid)
        if m.get("status") == "posted":
            raise RuntimeError("Already posted")
        store.update(cid, post={"state": "preparing", "pct": 0, "auto": auto})
        if not generate.is_generated(m):
            generate.generate_all(cid)
        ai = store.load(cid).get("ai") or {}
        if auto and not ai and config.load()["autopost"].get("require_check", True):
            store.update(cid, generated=False)              # redo description + check next time, when AI quota is back
            raise RuntimeError("Content check unavailable (AI limit or outage) - will retry later")
        if auto and (ai.get("safe") is False or ai.get("relevant") is False):   # never auto-post what the content check flagged
            raise RuntimeError("Skipped by content check: " + ("unsafe" if ai.get("safe") is False else "not about GTA 6")
                               + (f" ({ai['reason']})" if ai.get("reason") else ""))
        store.update(cid, post={"state": "uploading", "pct": 0, "auto": auto})
        youtube.upload(cid, progress=lambda p: store.update(cid, post={"state": "uploading", "pct": p, "auto": auto}))
        store.update(cid, post={"state": "done", "pct": 100, "auto": auto})
    except Exception as e:
        store.update(cid, post={"state": "error", "error": str(e)[:300], "pct": 0, "auto": auto, "at": time.time()})
        raise
    finally:
        _lock.release()


def post_async(cid, auto=False):
    if _lock.locked():
        raise RuntimeError("Another upload is in progress")
    def run():
        try:
            post(cid, auto)
        except Exception as e:
            import logging; logging.getLogger("clipforge").exception("post %s failed", cid)
    threading.Thread(target=run, daemon=True).start()
