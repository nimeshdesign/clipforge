"""Everything beyond the title is generated on demand: description, tags, content check, thumbnail."""
from . import config, store, seo, ai, thumbnails


def is_generated(m):
    return m.get("generated", bool(m.get("description")))


def generate_all(cid):
    cfg = config.load()
    m = store.load(cid)
    full = seo.generate({"transcript": m.get("transcript", ""), "duration": m["duration"], "source": m["source"]}, cfg)
    verdict = ai.judge(cfg["ai"]["gemini_text_model"], m.get("transcript", ""), m["source"].get("title", ""))
    store.update(cid, description=full["description"], tags=full["tags"], thumb_text=full["thumb_text"], ai=verdict)
    if cfg["thumbnail"]["enabled"]:
        try:
            thumbnails.make(cid)
        except Exception as e:
            print("thumbnail failed:", e)
    return store.update(cid, generated=True)
