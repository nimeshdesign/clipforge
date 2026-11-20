"""Gemini helpers over plain REST (no extra SDK): text judging + image generation ("Nano Banana")."""
import base64, json, os, re, urllib.request, urllib.error

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def key():
    return os.environ.get("AISTUDIOKEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _url(model):
    from . import config
    a = config.load().get("ai", {})
    if a.get("backend") == "vertex":   # bills against Google Cloud credits (incl. free trial)
        loc, proj = a.get("vertex_location", "global"), a["vertex_project"]
        host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
        return f"https://{host}/v1/projects/{proj}/locations/{loc}/publishers/google/models/{model}:generateContent"
    return f"{BASE}/{model}:generateContent"


_exhausted = {}   # model -> Pacific date on which its free-tier daily quota ran out


def _pacific_day():
    import datetime
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("America/Los_Angeles")).date()   # Gemini daily quotas reset at midnight Pacific


def _post(model, body, timeout=120, tag="misc"):
    from . import usage
    usage.check()                       # raises BudgetExceeded once the daily cap is hit
    if _exhausted.get(model) == _pacific_day():
        raise RuntimeError(f"{model}: free-tier daily limit reached, using free fallbacks until it resets (midnight US Pacific)")
    if "aiplatform" in _url(model):         # Vertex wants an explicit role on each content
        for c in body["contents"]:
            c.setdefault("role", "user")
    req = urllib.request.Request(_url(model), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key()})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        msg = e.read().decode(errors="replace")
        if re.search(r"per ?day|PerDay", msg, re.I):     # daily quota gone: stop calling this model until reset
            _exhausted[model] = _pacific_day()
            raise RuntimeError(f"{model}: free-tier daily limit reached, using free fallbacks until it resets (midnight US Pacific)")
        raise RuntimeError(f"{model}: free-tier rate limit hit, using a free fallback for this one")
    usage.record(model, r.get("usageMetadata"), tag)
    return r


def _img(b):
    return {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(b).decode()}}


def text_json(model, prompt, images=None, tag="misc", think=None):
    """Tries `model`, then each of ai.fallback_models when one is out of free quota, overloaded or retired.
    Free-tier limits are per model, so spreading calls across models keeps the AI free for longer."""
    from . import config, usage
    chain = [model] + [m for m in config.load().get("ai", {}).get("fallback_models") or [] if m != model]
    err = None
    for m in chain:
        try:
            return _text_json_one(m, prompt, images, tag, think)
        except usage.BudgetExceeded:
            raise
        except urllib.error.HTTPError as e:
            if e.code not in (404, 429, 500, 503):
                raise
            err = e
        except RuntimeError as e:           # free-tier limit reported by _post
            err = e
    raise err


def _text_json_one(model, prompt, images=None, tag="misc", think=None):
    """think: None = model default; "minimal"/"low" = cap hidden reasoning tokens (they are billed as output)."""
    parts = [{"text": prompt}] + [_img(b) for b in (images or [])]
    gc = {"responseMimeType": "application/json"}
    if think:
        gc["thinkingConfig"] = {"thinkingLevel": think}
    try:
        r = _post(model, {"contents": [{"parts": parts}], "generationConfig": gc}, tag=tag)
    except urllib.error.HTTPError as e:
        if not think or e.code != 400:
            raise
        gc.pop("thinkingConfig")             # model doesn't accept the thinking knob: retry plain
        r = _post(model, {"contents": [{"parts": parts}], "generationConfig": gc}, tag=tag)
    return json.loads(r["candidates"][0]["content"]["parts"][0]["text"])


def judge(model, transcript, source_title):
    """Is this clip really about GTA 6, and how clip-worthy is it? Returns dict or None without a key."""
    if not key():
        return None
    p = f"""You curate clips for a GTA 6 YouTube Shorts channel.
Source video: {source_title}
Clip transcript: {transcript[:1500] or '(no speech)'}
Return JSON: {{"relevant": true/false (is it about GTA 6 / its gameplay, trailers, news or leaks),
"viral_score": 0-10 (hook strength, excitement, standalone clarity), "safe": true/false (no slurs, hate, graphic content, doxxing),
"reason": "<=15 words"}}"""
    try:
        return text_json(model, p, tag="judge", think="minimal")
    except Exception:
        return None


def image(model, prompt, refs=None, aspect="9:16", tag="thumbnail"):
    """Returns image bytes from a Gemini image model, optionally conditioned on a reference frame."""
    parts = [{"text": prompt}] + [_img(b) for b in (refs or [])]
    r = _post(model, {"contents": [{"parts": parts}],
                      "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": aspect}}}, tag=tag)
    for part in r["candidates"][0]["content"]["parts"]:
        d = part.get("inlineData") or part.get("inline_data")
        if d:
            return base64.b64decode(d["data"])
    raise RuntimeError("no image returned")
