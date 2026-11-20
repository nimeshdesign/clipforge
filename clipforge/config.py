import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SOURCES = DATA / "sources"
LIVE = DATA / "live"
SECRETS = ROOT / "secrets"
CONFIG_FILE = ROOT / "config.yaml"          # your settings (git-ignored)
CONFIG_EXAMPLE = ROOT / "config.example.yaml"  # tracked defaults, copied on first run
PROMPTS = ROOT / "prompts"


def load():
    if not CONFIG_FILE.exists() and CONFIG_EXAMPLE.exists():
        CONFIG_FILE.write_text(CONFIG_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}


def save(cfg):
    CONFIG_FILE.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def clips_dir():
    p = Path(load().get("clips_dir", "clips"))
    p = p if p.is_absolute() else ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


(DATA / "prompts").mkdir(parents=True, exist_ok=True)
for d in (DATA, SOURCES, LIVE, SECRETS):
    d.mkdir(parents=True, exist_ok=True)

import os
# The static ffmpeg in ~/.local/bin segfaults on https/.ts inputs here; prefer the system build.
FFMPEG = "/usr/bin/ffmpeg" if os.path.exists("/usr/bin/ffmpeg") else "ffmpeg"


def _load_keys():
    f = SECRETS / "keys.env"   # lines like GEMINI_API_KEY=xxxx  /  ANTHROPIC_API_KEY=xxxx
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_keys()


def prompt(name):
    """Prompt template: your edited copy in data/prompts/ wins over the tracked default in prompts/."""
    mine = DATA / "prompts" / f"{name}.md"
    return (mine if mine.exists() else PROMPTS / f"{name}.md").read_text(encoding="utf-8")
