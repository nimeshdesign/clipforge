"""Record live streams (Twitch / Kick / YouTube) in gapless chunks with ffmpeg's segment muxer and
queue every finished chunk for clipping - so clips are ready while the stream is still running."""
import subprocess, sys, threading, time, re
from pathlib import Path
from . import config, pipeline, store, cleanup, discover

_procs = {}      # url -> Popen
_done = set()    # chunks already queued
_state = {}      # url -> status text
_starting = set()
_started = {}    # url -> when its recorder started
_quick = {}      # url -> recorders in a row that ended within QUICK_EXIT seconds
_cooldown = {}   # url -> time before which we don't try again
QUICK_EXIT, MAX_QUICK, COOLDOWN = 120, 3, 20 * 60   # a stream that keeps dying at once (ended/removed) waits 20 min


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
    _procs[url] = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
    _started[url] = time.time()
    _state[url] = "recording"


def tick():
    cfg = config.load()
    live = cfg["live"]
    streams = live.get("streams", []) + discover.live_streams()
    active = {s["url"] for s in streams if s.get("enabled", True)}
    for url in list(_procs):                      # stop removed / disabled streams, reap ended ones
        if url not in active:
            _procs.pop(url).terminate()
            _state.pop(url, None)
        elif _procs[url].poll() is not None:
            _procs.pop(url)
            _state[url] = "offline"
            if time.time() - _started.get(url, 0) < QUICK_EXIT:      # died right away: ended / removed stream
                _quick[url] = _quick.get(url, 0) + 1
                if _quick[url] >= MAX_QUICK:
                    _cooldown[url], _quick[url] = time.time() + COOLDOWN, 0
                    _state[url] = "offline (stream keeps stopping - retry in 20 min)"
                    import logging; logging.getLogger("clipforge").warning("live: %s keeps stopping right away, pausing 20 min", url)
            else:
                _quick[url] = 0
    for s in streams:
        if _cooldown.get(s["url"], 0) > time.time():
            continue
        if s["url"] in active and s["url"] not in _procs and s["url"] not in _starting:
            _starting.add(s["url"])
            def go(s=s):
                try:
                    start_recorder(s, live["chunk_seconds"])
                finally:
                    _starting.discard(s["url"])
            threading.Thread(target=go, daemon=True).start()
    # queue finished chunks (not modified for 20s, or recorder gone)
    for s in streams:
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
            if r["deleted"] or r["junk_chunks"] or r["sources_deleted"]:
                import logging
                logging.getLogger("clipforge").info(
                    "cleanup: %s old chunks (%s GB), %s junk fragments, %s old source files (%s GB); live folder now %s GB",
                    r["deleted"], r["freed_gb"], r["junk_chunks"], r["sources_deleted"], r["sources_freed_gb"], r["after_gb"])
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
