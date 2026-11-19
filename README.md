# ClipForge: AI livestream and video clipper for YouTube Shorts

**Turn any game video or live stream into captioned vertical Shorts with SEO titles, descriptions, tags and AI thumbnails, then review and post them with one click.** Ships tuned for GTA 6, and works for any game or streamer by editing the keyword list in `config.yaml`.

ClipForge is a self-hosted, open-source **AI video clipper**. It records live streams, downloads uploaded videos, finds the funniest and loudest moments, renders 9:16 Shorts with word-accurate captions and a face-cam layout, and uploads through the official **YouTube Data API v3**. A local web UI lets you preview every clip, edit the metadata and thumbnail prompt, and press **Post**.

> Keywords: AI video clipper, livestream clipper, YouTube Shorts automation, auto clip highlights, Opus Clip alternative, GTA 6 clips, Twitch and Kick VOD clipper, YouTube Data API upload, Gemini thumbnail generator, Nano Banana, faster-whisper captions, yt-dlp, FastAPI.

![status](https://img.shields.io/badge/status-beta-orange) ![python](https://img.shields.io/badge/python-3.10%2B-blue) ![license](https://img.shields.io/badge/license-MIT-green)

---

## Table of contents
1. [Features](#features)
2. [How it works](#how-it-works)
3. [Requirements](#requirements)
4. [Quick start](#quick-start)
5. [Get your API keys and enable Google features](#get-your-api-keys-and-enable-google-features)
   - [A. YouTube Data API and login](#a-youtube-data-api-v3-and-oauth-login-required-for-posting)
   - [B. Gemini API key (SEO text, humour agent, thumbnails)](#b-gemini-api-key-seo-text-humour-agent-thumbnails)
   - [C. Use your $300 Google Cloud trial credit (Vertex AI)](#c-use-your-300-google-cloud-trial-credit-vertex-ai)
   - [D. Optional: Anthropic key](#d-optional-anthropic-claude-key)
   - [E. Speech to text](#e-speech-to-text-no-key-needed)
6. [Using the web UI](#using-the-web-ui)
7. [Configuration reference](#configuration-reference)
8. [Cost control](#cost-control)
9. [Auto-posting rules](#auto-posting-rules)
10. [Project layout](#project-layout)
11. [Keeping your secrets safe](#keeping-your-secrets-safe)
12. [Troubleshooting](#troubleshooting)
13. [Copyright and channel safety](#copyright-and-channel-safety)
14. [Credits and licence](#credits-and-licence)

---

## Features
- **Two sources:** paste a YouTube, Twitch or Kick VOD link, or add live streams. Live streams are recorded in 10-minute chunks and clipped automatically. Several streams are processed in parallel.
- **Moment detection:** every second is scored from loudness, sudden jumps in volume, YouTube's "most replayed" heatmap and hype keywords. The best non-overlapping moments become clips.
- **Word-exact captions:** faster-whisper word timestamps drive per-word highlighted captions (ASS), so the text matches the voice.
- **Gameplay on top, face cam below:** OpenCV finds the streamer's face cam, the game is zoomed to fill the top panel and the cam is placed underneath. The detected box is cached in a `.layout.json` you can edit.
- **Humour agent (opt-in per stream):** one cheap Gemini call rates each 10-minute chunk, creates clips for the hype moments and marks them with a star.
- **SEO on demand:** only a title is written when a clip is created. Description, tags and thumbnail are generated when you open the clip and press **Generate**, so you never pay for clips you discard.
- **AI thumbnails:** a portrait thumbnail from Gemini image generation (Nano Banana). An art-director prompt, which you can edit in the UI, writes the brief. The watermark corner is kept empty and cropped away.
- **One-click YouTube upload:** log in once, preview in a dialog, edit, post. Resumable upload with progress and automatic thumbnail.
- **Auto-post guard rails:** at most 2 posts per 4 hours, plus an idle fallback that posts the best clip if nothing went out for a few hours.
- **Hard budget cap:** a local ledger tracks every AI call and stops at your daily limit (default $10).
- **Insights tab:** spend by purpose, clip funnel, per-stream stats, and a score breakdown for each clip.

## How it works
```
URL or live stream
  -> yt-dlp download / ffmpeg segment recorder
  -> signal scoring (loudness, jumps, heatmap, keywords)
  -> faster-whisper word timestamps
  -> face-cam detection and 9:16 render with burned captions
  -> [optional] humour agent rates the chunk and stars clips
  -> clips/<id>/{clip.mp4, title.txt, description.txt, tags.txt, meta.json}
  -> review in the web UI -> Generate (SEO + thumbnail) -> Post to YouTube
```

## Requirements
| Need | Why | Check |
|---|---|---|
| Python 3.10 or newer | app | `python3 --version` |
| ffmpeg (with libass) | recording and rendering | `ffmpeg -version` |
| Node.js | yt-dlp needs a JS runtime for YouTube | `node --version` |
| ~4 GB RAM, any CPU | Whisper `base.en` runs on CPU | |
| Disk space | live chunks are large; delete old ones in `data/live/` | |

Install on Debian/Ubuntu: `sudo apt install ffmpeg nodejs python3-venv`. On macOS: `brew install ffmpeg node`.

## Quick start
```bash
git clone <your-fork-url> clipforge
cd clipforge
cp secrets/keys.env.example secrets/keys.env     # then paste your Gemini key (see section B)
./run.sh                                         # installs dependencies, starts the server
```
Open <http://localhost:8800>. On first run `config.example.yaml` is copied to `config.yaml`, which is yours to edit (it is git-ignored).

`run.sh` uses [uv](https://docs.astral.sh/uv/) if installed, otherwise a normal `venv` + `pip`.

## Get your API keys and enable Google features

You need three things at most. Only **A** is required to post; the app still makes clips without any key (titles and SEO fall back to built-in templates, thumbnails to a frame with text).

**No keys at all? You can still clip.** Download, scoring, captions, 9:16 render and the review UI need no key. Without Gemini, titles and descriptions come from built-in templates, thumbnails are a game frame with a headline, and the humour agent is skipped (clips are kept by score). Add a key later and nothing else changes.

| Feature | What you need | Cost |
|---|---|---|
| Post to YouTube | OAuth client (section A) | free, 10,000 quota units/day |
| SEO text, humour agent, thumbnails | Gemini key (B) or Vertex AI (C) | cents per day, capped by you |
| Claude-written SEO (optional) | Anthropic key (D) | pay per use |
| Captions | none, runs locally (E) | free |

### A. YouTube Data API v3 and OAuth login (required for posting)
1. Open the [Google Cloud console](https://console.cloud.google.com/) and sign in with the Google account that owns your YouTube channel.
2. Click the project picker at the top, then **New project**. Name it (for example `gta6-clipper`) and create it. Make sure it is selected.
3. Go to **APIs & Services > Library**, search **YouTube Data API v3** and click **Enable**.
4. Go to **APIs & Services > OAuth consent screen** (newer consoles call it **Google Auth Platform**).
   - **Branding / App information:** app name `ClipForge`, your email as support and developer contact.
   - **Audience:** user type **External**. Leave the publishing status as **Testing**.
   - **Test users:** click **Add users** and add the Google account you will log in with. **This step is the fix for "Access blocked: ClipForge has not completed the Google verification process".** Only listed test users can log in while the app is in Testing.
   - **Data access (scopes):** add `https://www.googleapis.com/auth/youtube.upload` and `https://www.googleapis.com/auth/youtube.readonly`.
5. Go to **APIs & Services > Credentials > Create credentials > OAuth client ID**. Choose application type **Desktop app**, name it, create it, then **Download JSON**.
6. Save the file as `secrets/client_secret.json` in this project.
7. Start the app and click **Log in with YouTube** in the header. A browser tab opens on port 8765 for Google sign-in. Click through the "Google hasn't verified this app" notice (**Advanced > Continue**), which is expected for your own app in Testing mode, and approve the two permissions. A refresh token is stored in `secrets/token.json`.

Things to know:
- **Quota:** each upload costs about 1,600 units and each thumbnail 50 units. The default 10,000 units/day allows about 6 uploads. `youtube.max_uploads_per_day` guards this. Ask for more with Google's quota extension form.
- **Private-only uploads:** videos uploaded through API projects that have not passed Google's [API compliance audit](https://support.google.com/youtube/contact/yt_api_form) can be forced to **private**. Submit the audit form if your posts stay private, or publish them from YouTube Studio.
- **Test-mode tokens expire after 7 days.** If posting stops with an auth error, click **log out**, then log in again.
- **Custom thumbnails** require your channel to be verified (phone verification in YouTube Studio).

### B. Gemini API key (SEO text, humour agent, thumbnails)
The easiest route is Google AI Studio.
1. Open [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and click **Create API key** (choose or create a Cloud project).
2. Put it in `secrets/keys.env`:
   ```
   GEMINI_API_KEY=your-key-here
   ```
   The app also reads `AISTUDIOKEY` or `GOOGLE_API_KEY`.
3. Make sure `ai.backend: aistudio` in `config.yaml` (the default).
4. Text calls (titles, SEO, humour agent) work on the free tier. **Image generation (Nano Banana) needs billing enabled** on the project: AI Studio > **Set up billing** on the key. Prepaid AI Studio credits work, but the Google Cloud free-trial credit does **not** apply to AI Studio. For that, use section C.

Models used (editable in `config.yaml`): text `gemini-3-flash-preview`, image `gemini-2.5-flash-image`. A thumbnail costs roughly 3 cents; a title well under 0.1 cent.

### C. Use your $300 Google Cloud trial credit (Vertex AI)
The free-trial credit says "not applicable to Gemini API" because that label refers to AI Studio. The same models run through **Vertex AI**, which the credit does cover.
1. In the Cloud console, open the project you used in section A and make sure billing is linked (the trial account).
2. Go to **APIs & Services > Library**, search **Vertex AI API** and click **Enable**.
3. Go to **APIs & Services > Credentials > Create credentials > API key**.
4. Open the new key and click **Restrict key**. Under **API restrictions** choose **Restrict key** and tick **Vertex AI API** (it may be listed as *Agent Platform API*). Save. Restricting to a different API gives `403 ... blocked`.
5. Put the key in `secrets/keys.env` as `GEMINI_API_KEY=...`.
6. Find your **project number** (Cloud console home > Project info) and set it in `config.yaml`:
   ```yaml
   ai:
     backend: vertex
     vertex_project: "123456789012"
     vertex_location: global
   ```
7. Restart. The Insights tab and the header pill show estimated spend either way.

### D. Optional: Anthropic (Claude) key
If you prefer Claude to write the SEO text, add `ANTHROPIC_API_KEY=` to `secrets/keys.env`. It is used for titles/descriptions/tags only; the humour agent and thumbnails stay on Gemini. Skip this if you are happy with Gemini.

### E. Speech to text (no key needed)
Captions use [faster-whisper](https://github.com/SYSTRAN/faster-whisper) locally. The model downloads on first use. Set `asr.model` to `small.en` for better accuracy on noisy streams (slower), or `base.en` (default) for speed. No account, no key, no cost.

## Using the web UI
| Tab | What it does |
|---|---|
| **Clips** | Grid of all clips. Filter by starred or by source (uploaded video or a specific live stream). Sort by newest, humour rating, clip score or starred first. Click a clip to open the review dialog. |
| **Sources & jobs** | Paste a video URL, add live streams, toggle the humour agent per stream, watch job progress. |
| **Insights** | Clip funnel, AI spend today by purpose, 7-day spend, per-stream stats. |
| **Prompts** | Edit the thumbnail art-director prompt used for every new thumbnail. Reset to the default at any time. |

**Review dialog:** preview the video, see why it was picked (score breakdown, humour agent rating, caption text), edit title, description and tags, press **Generate** to create SEO text and a thumbnail, edit the thumbnail brief and re-make it, choose privacy, then **Post to YouTube**, or **Approve** it for the auto-post queue.

## Configuration reference
Everything lives in `config.yaml` (copied from [`config.example.yaml`](config.example.yaml)). Highlights:

| Key | Meaning |
|---|---|
| `workers` | Clip jobs processed at once (Whisper itself runs one at a time). |
| `clip.mode` | `shorts` (9:16) or `landscape`. |
| `clip.min_seconds / target_seconds / max_seconds` | Clip length range. |
| `clip.max_per_source`, `clip.min_score` | How many clips per video and the minimum score. |
| `weights`, `keywords` | Signal weights and hype words for moment scoring. |
| `live.chunk_seconds` | Length of each recorded live chunk (default 600). |
| `youtube.privacy` | Default privacy for uploads: `private`, `unlisted` or `public`. |
| `youtube.max_uploads_per_day` | Safety limit to protect your API quota. |
| `autopost.*` | Auto-post limits (below). |
| `agent.*` | Humour agent threshold (`min_hype`), candidates per chunk, caption size. |
| `budget.daily_usd` | Hard daily AI cap. |
| `thumbnail.corner_crop` | Fraction kept when cropping the Gemini watermark corner. |

## Cost control
- Titles are the only thing generated automatically (a fraction of a cent each).
- The humour agent sends only about 200 characters of captions for at most 6 candidate moments per chunk, one call per chunk, with Gemini thinking set to minimal.
- Every call is logged in `data/usage.json`. When estimated spend reaches `budget.daily_usd`, AI calls stop and free fallbacks are used. Prices are estimates in `budget.prices_per_million`, so adjust them if Google changes rates.

## Auto-posting rules
Switch **Auto-post** on in the header. The scheduler checks every minute and:
- posts at most `max_per_window` clips per `window_hours` (default 2 per 4 hours) with at least `min_gap_minutes` between posts;
- prefers **approved** clips, then starred clips, then by score;
- if nothing has been posted for `idle_hours` (default 3), posts the best remaining clip;
- never exceeds `youtube.max_uploads_per_day`.

## Project layout
```
clipforge/
  server.py        FastAPI app and REST API
  pipeline.py      job queue and the clip pipeline
  live.py          live stream recorder and chunk queue
  analyze.py       moment scoring and Whisper transcription
  render.py        ffmpeg render and word-synced captions
  layout.py        face-cam detection
  agent.py         humour agent (one call per chunk)
  seo.py           title, description, tags
  thumbnails.py    two-step Gemini thumbnails
  ai.py, usage.py  Gemini client and budget ledger
  youtube.py       OAuth and resumable upload
  poster.py, autopost.py   upload worker and scheduler
  store.py         clip folders and metadata
  web/index.html   the UI (single file, no build step)
prompts/           default thumbnail art-director prompt
config.example.yaml   default settings
secrets/           your keys (git-ignored, see below)
clips/, data/      output and runtime data (git-ignored)
```

## Keeping your secrets safe
- `secrets/*` (except `*.example` and the README), `config.yaml`, `data/` and `clips/` are in [`.gitignore`](.gitignore). The OAuth client JSON, `token.json`, API keys, channel name and your stream list never reach git.
- Before publishing, run `git status` and `git ls-files secrets` (it must list only `README.md` and `keys.env.example`).
- If a key ever leaks, delete it in the Cloud console (Credentials) and create a new one. For `token.json`, revoke access at <https://myaccount.google.com/permissions>.
- The server binds to `127.0.0.1` only. Do not expose port 8800 to the internet: the API can post to your channel and has no login.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Access blocked: ... has not completed the Google verification process` | Add your account under OAuth consent screen > **Test users** (section A, step 4). |
| `403 ... API has not been used` | Enable YouTube Data API v3 / Vertex AI API on the right project. |
| Gemini `403 ... blocked` | The key's API restriction does not include the API you call (section C, step 4). |
| Gemini `402` / prepayment error | The trial credit does not cover AI Studio. Use Vertex (section C). |
| Uploads stay private | API audit required (section A, "Private-only uploads"). |
| `ffmpeg` crashes on live URLs | Use your distro's ffmpeg, not a static build. |
| yt-dlp "format not available" | Install Node.js and update yt-dlp: `pip install -U yt-dlp`. |
| Posting asks you to log in again after a week | Testing-mode tokens expire in 7 days. Log in again. |

## Copyright and channel safety
Clipping other people's videos can lead to Content ID claims, copyright strikes or "reused content" demonetisation. Use sources you have permission for, Rockstar's official trailers under their fan-content policy, or your own footage. Every description credits the source channel (`seo.credit_source`). Start with `privacy: private`, review clips, and keep upload volume modest. This project is not affiliated with Rockstar Games or Take-Two Interactive; Grand Theft Auto is their trademark.

## Credits and licence
This project is released under the [MIT licence](LICENSE). Third-party notices are in [NOTICE.md](NOTICE.md).

The code in this repository was written independently. The following open-source projects were **studied for ideas** (no source code was copied); they deserve credit for the approaches:

| Project | Licence | Idea we learned from |
|---|---|---|
| [R3LAMP4GO/clipping-gta-6](https://github.com/R3LAMP4GO/clipping-gta-6) | AGPL-3.0 | multi-signal moment scoring (loudness, jumps, "most replayed" heatmap, hype words) and chunked live recording |
| [ColinGPT9/clips-studio](https://github.com/ColinGPT9/clips-studio) | AGPL-3.0 | job-queue design and gaming layout ideas |
| [mutonby/openshorts](https://github.com/mutonby/openshorts) | MIT | LLM-written metadata and the review-then-post flow |
| [lxgicstudios/lxgic-clipper](https://github.com/lxgicstudios/lxgic-clipper) | MIT | the record, detect, clip, post pipeline shape |

Built on [FastAPI](https://fastapi.tiangolo.com/), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [FFmpeg](https://ffmpeg.org/), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [OpenCV](https://opencv.org/), the [YouTube Data API](https://developers.google.com/youtube/v3) and Google Gemini.
