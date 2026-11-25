"""SEO title / description / tags for every clip.
Uses Claude when ANTHROPIC_API_KEY is set, otherwise a built-in template generator."""
import os, re, json, collections, string
from . import config, ai

STOP = set("the a an and or but if of to in on at for with is it this that was are be as i you we they he she my your so just like im its not do dont have has had what when how then than there their".split())


def _tags_fit(tags, limit=480):
    out, total = [], 0
    for t in tags:
        t = t.strip()
        if not t or t.lower() in (x.lower() for x in out):
            continue
        if total + len(t) + 1 > limit:
            break
        out.append(t)
        total += len(t) + 1
    return out


CREDIT_MARK = "🎥 Credit:"
_OLD_CREDIT = re.compile(r"\n*Source: [^\n]*\nhttps?://\S+\s*$")          # pre-2026-11-24 footer
_NEW_CREDIT = re.compile(r"^" + re.escape(CREDIT_MARK) + r".*?(\n\n|$)", re.S)


def creator(cfg, src):
    """(name, channel_url) of the creator a clip comes from. Live streams store channel='live' and the stream
    name as title ("Cinna live"), so they are matched to discover.channels by URL or by name."""
    url = (src.get("url") or "").strip()
    chans = (cfg.get("discover") or {}).get("channels") or []
    for ch in chans:
        base = ch["url"].rstrip("/")
        if url.startswith(base):
            return ch["name"], base
    name = (src.get("channel") or "").strip()
    if name.lower() in ("", "live"):
        name = re.sub(r"\s*(gta ?6\s*)?live$", "", (src.get("title") or "").strip(), flags=re.I).strip()
    for ch in chans:
        if ch["name"].lower() == name.lower():
            return ch["name"], ch["url"].rstrip("/")
    return name, None


def credit_block(cfg, src):
    """Shown first in the description (above the fold), so the original creator is visible on every Short."""
    name, chan = creator(cfg, src)
    url = (src.get("url") or "").strip()
    if not name and not url:
        return ""
    handle = re.search(r"youtube\.com/(@[\w.-]+)", chan or url or "")
    who = handle.group(1) if handle and handle.group(1).lower().lstrip("@") == name.lower().replace(" ", "") else name
    lines = [f"{CREDIT_MARK} {who or 'original creator'} - all rights belong to the original creator"]
    if chan and chan not in url:
        lines.append(f"📺 Channel: {chan}")
    if url:
        lines.append(("🔴 Original stream: " if "/live" in url or "live" == (src.get("channel") or "").strip().lower() else "▶️ Original video: ") + url)
    return "\n".join(lines) + "\n\n"


def with_credit(cfg, description, src):
    """Idempotent: strips any earlier credit (old footer or new header) and puts the current one on top."""
    d = _NEW_CREDIT.sub("", _OLD_CREDIT.sub("", description or "")).strip()
    if not cfg["seo"].get("credit_source", True):
        return d
    return (credit_block(cfg, src) + d).strip()


def _template(cfg, ctx):
    s = cfg["seo"]
    text = ctx["transcript"]
    words = [w for w in re.findall(r"[a-zA-Z']{4,}", text.lower()) if w not in STOP]
    top = [w for w, _ in collections.Counter(words).most_common(6)]
    first = re.split(r"(?<=[.!?])\s+", text.strip())[0] if text.strip() else ""
    first = re.split(r"[,.!?]", first)[0].strip()          # first phrase only, no stutter repeats
    hook = first if len(first) > 12 else "Insane Moment"
    if len(hook) > 55:
        hook = hook[:55].rsplit(" ", 1)[0]                   # cut on a word boundary
    title = f"GTA 6: {string.capwords(hook)}"
    desc = (f"{hook}. Best GTA 6 moments, gameplay and trailer reactions.\n\n"
            f"{text[:300].strip()}\n\nSubscribe for daily GTA 6 clips, news and leaks.\n\n"
            + " ".join(s["base_hashtags"]))
    tags = s["base_tags"] + top
    return title, desc, tags, " ".join(hook.split()[:3])


def _claude(cfg, ctx):
    import anthropic
    s = cfg["seo"]
    prompt = f"""You write YouTube Shorts metadata for a GTA 6 clips channel. Optimise for search and click-through, no clickbait lies.
Clip transcript: {ctx['transcript'][:1500] or '(no speech)'}
Source video: {ctx['source'].get('title','')} by {ctx['source'].get('channel','')}
Clip length: {ctx['duration']}s
Return ONLY JSON: {{"title": "<=70 chars, main keyword 'GTA 6' near the start, strong hook", "description": "3-4 sentences with natural keywords, a call to subscribe, then 3-5 hashtags incl #GTA6 #Shorts", "tags": ["15-20 tags, mix of broad and long-tail, no # sign"], "thumb_text": "2-3 punchy words for the thumbnail"}}"""
    if os.environ.get("ANTHROPIC_API_KEY"):
        r = anthropic.Anthropic().messages.create(model=s["model"], max_tokens=700, messages=[{"role": "user", "content": prompt}])
        j = json.loads(re.search(r"\{.*\}", r.content[0].text, re.S).group(0))
    else:
        j = ai.text_json(cfg["ai"]["gemini_text_model"], prompt, tag="seo")
    return j["title"], j["description"], j["tags"], j.get("thumb_text", "GTA 6")


def generate(ctx, cfg=None):
    """ctx: transcript, duration, source{title,channel,url}. Returns dict(title, description, tags)."""
    cfg = cfg or config.load()
    use_ai = cfg["seo"]["provider"] != "template" and (os.environ.get("ANTHROPIC_API_KEY") or ai.key())
    try:
        title, desc, tags, thumb = _claude(cfg, ctx) if use_ai else _template(cfg, ctx)
    except Exception:
        title, desc, tags, thumb = _template(cfg, ctx)
    if "#shorts" not in desc.lower() and cfg["clip"]["mode"] == "shorts":
        desc += " #Shorts"
    name = creator(cfg, ctx["source"])[0] if cfg["seo"].get("credit_source", True) else ""
    return {"title": title[:100], "description": with_credit(cfg, desc, ctx["source"]),
            "tags": _tags_fit(tags + cfg["seo"]["base_tags"] + ([name] if name else [])), "thumb_text": thumb}


def generate_title(ctx, cfg=None):
    """Cheap first pass at clip creation: a title only (one tiny AI call, or the free template)."""
    cfg = cfg or config.load()
    t = None
    if cfg["seo"]["provider"] != "template" and (os.environ.get("ANTHROPIC_API_KEY") or ai.key()):
        try:
            p = (f"Write one YouTube Shorts title (<=70 chars, starts with or contains 'GTA 6', strong hook, no clickbait lies) "
                 f"for a clip with this transcript: {ctx['transcript'][:600] or '(no speech)'}\nReturn ONLY JSON {{\"title\": \"...\"}}")
            t = ai.text_json(cfg["ai"]["gemini_text_model"], p, tag="title", think="minimal")["title"] if ai.key() else None
        except Exception:
            t = None
    return (t or _template(cfg, ctx)[0])[:100]
