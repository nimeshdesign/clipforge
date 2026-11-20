"""Record live streams (Twitch / Kick / YouTube) in gapless chunks with ffmpeg's segment muxer and
queue every finished chunk for clipping - so clips are ready while the stream is still running."""
import subprocess, sys, threading, time, re
from pathlib import Path
from . import config, pipeline, store, cleanup

_procs = {}      # url -> Popen
_done = set()    # chunks already queued
_state = {}      # url -> status text
_starting = set()


def slug(u):
    return re.sub(r"[^a-z0-9]+", "-", u.lower()).strip("-")[-40:]


def stream_url(u):
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--js-runtimes", "node", "-g", "-f", "bv*[height<=720]+ba/b[height<=720]/b", "--no-playlist", u],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return r.stdout.strip().splitlines() if r.returncode == 0 and r.stdout.strip() else None  # [video, audio] or [muxed]


def start_recorder(entry, chunk):
    url = entry["url"]
    direct = stream_url(url)
    if not direct:
        _state[url] = "offline"
        return
    d = config.LIVE / slug(url)
    d.mkdir(parents=True, exist_ok=True)
    ins = [x for u in direct for x in ("-i", u)]
    cmd = [config.FFMPEG, "-v", "error", *ins, "-c", "copy", "-f", "segment", "-segment_time", str(chunk),
           "-reset_timestamps", "1", "-strftime", "1", str(d / "chunk_%Y%m%d-%H%M%S.ts")]
    _procs[url] = subprocess.Popen(cmd)
    _state[url] = "recording"


def tick():
    cfg = config.load()
    live = cfg["live"]
    active = {s["url"] for s in live.get("streams", []) if s.get("enabled", True)}
    for url in list(_procs):                      # stop removed / disabled streams, reap ended ones
        if url not in active:
            _procs.pop(url).terminate()
            _state.pop(url, None)
        elif _procs[url].poll() is not None:
            _procs.pop(url)
            _state[url] = "offline"
    for s in live.get("streams", []):
        if s["url"] in active and s["url"] not in _procs and s["url"] not in _starting:
            _starting.add(s["url"])
            def go(s=s):
                try:
                    start_recorder(s, live["chunk_seconds"])
                finally:
                    _starting.discard(s["url"])
            threading.Thread(target=go, daemon=True).start()
    # queue finished chunks (not modified for 20s, or recorder gone)
    for s in live.get("streams", []):
        d = config.LIVE / slug(s["url"])
        if not d.exists():
            continue
        files = sorted(d.glob("chunk_*.ts"))
        for f in files:
            if f in _done or store.is_processed(f.resolve()):
                _done.add(f)
                continue
            finished = time.time() - f.stat().st_mtime > 20
            if finished and f.stat().st_size > 1_000_000:
                _done.add(f)
                pipeline.new_job("file", str(f), title=s.get("name") or s["url"], channel="live", url=s["url"], live=True, agent=bool(s.get("star_agent")))


def status():
    return _state


def loop():
    while True:
        try:
            tick()
            r = cleanup.run()
            if r["deleted"]:
                print(f"cleanup: freed {r['freed_gb']} GB ({r['deleted']} old chunks), live folder now {r['after_gb']} GB")
        except Exception as e:
            import logging; logging.getLogger("clipforge").exception("live tick error")
        time.sleep(config.load()["live"]["check_every_seconds"])


def kill_orphans():
    """Stop recorders left over from a previous run. On Windows a killed server leaves its ffmpeg children running,
    and every restart would otherwise add another recorder writing the same stream (duplicate chunks and clips)."""
    import logging, os
    live = str(config.LIVE.resolve())
    try:
        if os.name == "nt":
            ps = (f"$p = Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | Where-Object {{ $_.CommandLine -like '*{live}*' }}; "
                  "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; @($p).Count")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
            n = int((r.stdout.strip() or "0").splitlines()[-1])
        else:
            n = 0 if subprocess.run(["pkill", "-f", f"ffmpeg.*{re.escape(live)}"], capture_output=True, timeout=30).returncode else 1
        if n:
            logging.getLogger("clipforge").info("stopped %s leftover live recorder(s) from a previous run", n)
    except Exception:
        logging.getLogger("clipforge").exception("could not clean up leftover recorders")


def start():
    kill_orphans()
    threading.Thread(target=loop, daemon=True).start()
