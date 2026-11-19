"""Keep data/live/ under a size cap by deleting raw stream chunks that are no longer needed.

A chunk is deletable only when it has already been clipped (it is in data/processed.json) and is not being
written. Oldest chunks go first, until the folder is at or below live.max_keep_gb. Unprocessed chunks are never
touched, so if they alone exceed the cap you get a warning instead of lost footage.
Finished clips are separate files in clips/ and are never affected.

Run by hand:   python -m clipforge.cleanup [--dry-run] [--max-gb 10]
It also runs automatically every few minutes while the server is up."""
import argparse, time
from . import config, store

ACTIVE_SECONDS = 180          # a file modified this recently may still be recording


def _files():
    return [f for f in config.LIVE.rglob("chunk_*.ts") if f.is_file()]


def _size(f):
    sidecar = f.with_suffix(".layout.json")
    return f.stat().st_size + (sidecar.stat().st_size if sidecar.exists() else 0)


def run(max_gb=None, dry_run=False):
    cap = int(float(max_gb if max_gb is not None else config.load()["live"].get("max_keep_gb", 10)) * 1e9)
    files = _files()
    total = sum(f.stat().st_size for f in files)
    freed, removed = 0, []
    if total > cap:
        now = time.time()
        old_first = sorted(files, key=lambda f: f.stat().st_mtime)
        for f in old_first:
            if total - freed <= cap:
                break
            if now - f.stat().st_mtime < ACTIVE_SECONDS or not store.is_processed(f.resolve()):
                continue
            freed += _size(f)
            removed.append(f)
            if not dry_run:
                f.unlink(missing_ok=True)
                f.with_suffix(".layout.json").unlink(missing_ok=True)
    left = total - freed
    return {"before_gb": round(total / 1e9, 2), "after_gb": round(left / 1e9, 2), "freed_gb": round(freed / 1e9, 2),
            "deleted": len(removed), "cap_gb": round(cap / 1e9, 2), "over_cap": left > cap, "dry_run": dry_run}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-gb", type=float)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = run(a.max_gb, a.dry_run)
    print(r)
    if r["over_cap"]:
        print("Still over the cap: the rest are unprocessed or still recording. They will be removed once clipped.")
