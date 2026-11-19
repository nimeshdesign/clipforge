"""Post one clip: thumbnail (optional) -> YouTube upload, with status the UI can poll. One post at a time."""
import threading
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
        store.update(cid, post={"state": "uploading", "pct": 0, "auto": auto})
        youtube.upload(cid, progress=lambda p: store.update(cid, post={"state": "uploading", "pct": p, "auto": auto}))
        store.update(cid, post={"state": "done", "pct": 100, "auto": auto})
    except Exception as e:
        store.update(cid, post={"state": "error", "error": str(e)[:300], "pct": 0, "auto": auto})
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
            print("post failed:", e)
    threading.Thread(target=run, daemon=True).start()
