"""Token ledger + daily budget. Every Gemini call is recorded (tokens, estimated USD, purpose) in data/usage.json.
When today's estimated spend reaches budget.daily_usd, further AI calls raise BudgetExceeded and callers fall back to free paths."""
import datetime, json, threading
from . import config

F = config.DATA / "usage.json"
_lock = threading.Lock()


class BudgetExceeded(Exception):
    pass


def _load():
    try:
        return json.loads(F.read_text())
    except Exception:
        return {}


def _today():
    return datetime.date.today().isoformat()


def price(model):
    p = config.load()["budget"]["prices_per_million"]
    return p.get(model) or p["default"]


def cost(model, meta):
    pr = price(model)
    inp = meta.get("promptTokenCount", 0)
    out = meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)
    img = sum(d.get("tokenCount", 0) for d in meta.get("candidatesTokensDetails", []) if d.get("modality") == "IMAGE")
    usd = inp * pr["in"] / 1e6 + (out - img) * pr["out"] / 1e6 + img * pr.get("image_out", pr["out"]) / 1e6
    return usd, inp, out


def spent():
    return _load().get(_today(), {}).get("usd", 0.0)


def cap():
    return float(config.load()["budget"]["daily_usd"])


def check(est=0.0):
    if spent() + est >= cap():
        raise BudgetExceeded(f"daily AI budget ${cap():.2f} reached (spent ${spent():.2f})")


def record(model, meta, tag):
    usd, i, o = cost(model, meta or {})
    with _lock:
        d = _load()
        day = d.setdefault(_today(), {"usd": 0.0, "in": 0, "out": 0, "calls": 0, "by": {}})
        day["usd"] = round(day["usd"] + usd, 6)
        day["in"] += i
        day["out"] += o
        day["calls"] += 1
        b = day["by"].setdefault(tag, {"usd": 0.0, "calls": 0})
        b["usd"] = round(b["usd"] + usd, 6)
        b["calls"] += 1
        for k in sorted(d)[:-30]:       # keep 30 days
            d.pop(k)
        F.write_text(json.dumps(d, indent=1))


def summary():
    d = _load()
    t = d.get(_today(), {"usd": 0, "calls": 0, "in": 0, "out": 0, "by": {}})
    return {"spent": round(t["usd"], 4), "cap": cap(), "calls": t["calls"], "tokens_in": t["in"], "tokens_out": t["out"],
            "by": t["by"], "warn": t["usd"] >= cap() * config.load()["budget"]["warn_at"],
            "days": {k: round(v["usd"], 3) for k, v in sorted(d.items())[-7:]}}
