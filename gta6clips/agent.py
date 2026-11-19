"""Humour / hype agent. For one live chunk it gets the loudest candidate moments (already found locally, free) with a
short caption each, and asks the cheap model ONE question: which of these are really hype / funny?
Token savers: local pre-filter, only top-N moments, captions truncated, compact line format, tiny JSON answer."""
import re
from . import ai, config, usage

PROMPT = ("Rate GTA 6 stream moments for hype/humour 0-10 (10=hilarious, shocking or epic; 0=dead air, boring, off-topic). "
          "Lines are id|loudness 0-9|caption. Reply ONLY JSON [{\"i\":id,\"s\":score,\"l\":\"3-6 word label\"}].\n")


def rate(wins, cfg):
    """Adds win['hype'], win['label'], win['star'] in place. Returns the number of starred moments, or None if the AI was unavailable."""
    a = cfg["agent"]
    cand = sorted(wins, key=lambda w: -w["score"])[: a["max_candidates"]]
    if not cand:
        return 0
    lines = []
    for n, w in enumerate(cand):
        text = re.sub(r"\s+", " ", w.get("transcript", "")).strip()[: a["caption_chars"]] or "(no speech)"
        lines.append(f"{n}|{int(min(9, w['signal'] * 9))}|{text}")
    try:
        res = ai.text_json(a["model"], PROMPT + "\n".join(lines), tag="agent", think="minimal")
    except usage.BudgetExceeded:
        return None
    except Exception:
        return None
    for r in res if isinstance(res, list) else []:
        try:
            w = cand[int(r["i"])]
            w["hype"] = float(r["s"])
            w["label"] = str(r.get("l", ""))[:60]
            w["star"] = w["hype"] >= a["min_hype"]
        except Exception:
            continue
    return sum(1 for w in cand if w.get("star"))
