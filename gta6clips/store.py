"""Clips live on disk: clips/<id>/{clip.mp4, meta.json, title.txt, description.txt, tags.txt}."""
import json, shutil, threading
from . import config


_lock = threading.RLock()


def folder(cid):
    return config.clips_dir() / cid


def save(cid, meta):
    with _lock:
        _save(cid, meta)


def _save(cid, meta):
    d = folder(cid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    (d / "title.txt").write_text(meta["title"])
    (d / "description.txt").write_text(meta["description"])
    (d / "tags.txt").write_text(", ".join(meta["tags"]))


def load(cid):
    return json.loads((folder(cid) / "meta.json").read_text())


def all_clips():
    out = []
    for d in sorted(config.clips_dir().iterdir(), reverse=True):
        if (d / "meta.json").exists():
            out.append(json.loads((d / "meta.json").read_text()))
    return out


def delete(cid):
    shutil.rmtree(folder(cid), ignore_errors=True)


def update(cid, **kw):
    """Atomic read-modify-write (several threads touch a clip: poster, thumbnailer, UI)."""
    with _lock:
        m = load(cid)
        m.update(kw)
        _save(cid, m)
        return m


# ---- which source files (live chunks / downloads) were already analysed - survives restarts
_PF = config.DATA / "processed.json"


def _processed():
    try:
        return set(json.loads(_PF.read_text()))
    except Exception:
        return set()


def is_processed(path):
    return str(path) in _processed()


def mark_processed(path):
    with _lock:
        p = _processed()
        p.add(str(path))
        _PF.write_text(json.dumps(sorted(p)))
