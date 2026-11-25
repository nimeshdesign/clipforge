"""Put the creator credit (see seo.credit_block) at the top of every already-posted video's description.

    python -m clipforge.credit --dry-run   # show what would change
    python -m clipforge.credit             # update on YouTube (videos.update, ~50 quota units per video)

Only the description changes; title, tags, category, privacy and any publish schedule are kept."""
import sys
from . import config, store, seo, youtube


def run(dry=False):
    cfg = config.load()
    clips = [c for c in store.all_clips() if c.get("youtube_id")]
    if not dry and not youtube.has_scope(youtube.EDIT_SCOPE):
        print("The YouTube sign-in can upload but not edit videos. Sign in again (UI: YouTube -> Connect, or "
              "`python -m clipforge.youtube auth`) and allow 'manage your YouTube videos', then run this again.")
        return
    yt = None if dry else youtube._yt()
    done = skipped = failed = 0
    for c in clips:
        want = seo.with_credit(cfg, c.get("description", ""), c.get("source") or {})
        if dry:
            print(("UPDATE " if want != c.get("description") else "ok     ") + c["youtube_id"], "|", want.splitlines()[0][:90])
            continue
        try:
            items = yt.videos().list(part="snippet", id=c["youtube_id"]).execute().get("items") or []
            if not items:
                print("missing", c["youtube_id"], "(deleted or not visible)")
                failed += 1
                continue
            sn = items[0]["snippet"]
            new = seo.with_credit(cfg, sn.get("description", ""), c.get("source") or {})[:4900]
            if new == sn.get("description", ""):
                skipped += 1
                continue
            body = {"id": c["youtube_id"], "snippet": {k: sn[k] for k in ("title", "categoryId", "tags", "defaultLanguage",
                                                                         "defaultAudioLanguage") if k in sn}}
            body["snippet"]["description"] = new
            yt.videos().update(part="snippet", body=body).execute()
            store.update(c["id"], description=new)
            print("updated", c["youtube_id"], "|", new.splitlines()[0][:90])
            done += 1
        except Exception as e:
            print("FAILED", c["youtube_id"], str(e)[:200])
            failed += 1
            if "quotaExceeded" in str(e):
                print("YouTube quota used up for today - run again after midnight Pacific time")
                break
    if not dry:
        print(f"{done} updated, {skipped} already credited, {failed} failed")


if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv)
