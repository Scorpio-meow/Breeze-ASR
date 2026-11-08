# Setup and troubleshooting

Install, start, update and fix a Breeze ASR service. Full documentation lives in the repository: <https://github.com/Scorpio-meow/Breeze-ASR/tree/main/docs> (`deployment.md`, `configuration.md`, `api.md`, `cli.md`).

Contents: [Requirements](#requirements) · [Install](#install) · [Start and stop](#start-and-stop) · [First start](#first-start) · [Update](#update) · [Errors](#errors) · [Logs](#logs) · [Other devices](#other-devices) · [Watched folder](#watched-folder)

Installing, updating and reconfiguring change the user's machine. Explain what you are about to run and get a yes first.

## Requirements

| Item | Requirement |
| --- | --- |
| OS | Windows x64 or Linux natively; anything that runs Docker. Windows on ARM64 cannot install it. |
| Memory | 16 GB recommended. Converting models peaks around 9.5 GB; 8 GB works but swaps heavily. |
| Disk | About 16 GB, plus 4–7 GB temporarily during the first auto-tune. |
| Acceleration | Optional: Intel iGPU or Arc (OpenVINO, native only), NVIDIA (CUDA), AMD (ROCm, Linux only). Intel NPU and Qualcomm NPUs/GPUs are not used. Docker Desktop is CPU only. |
| Python | 3.12, installed automatically by uv. |

## Install

The repository is <https://github.com/Scorpio-meow/Breeze-ASR>. Clone or download it, then in its root:

| Platform | Command | Notes |
| --- | --- | --- |
| Windows | `powershell -ExecutionPolicy Bypass -File scripts\install.ps1` | Installs uv, the Python environment and `.env`, and a desktop shortcut "Breeze ASR". Picks CUDA when `nvidia-smi` exists. Options: `-Device cpu`/`cuda`, `-NoShortcut`. |
| Linux | `bash scripts/install.sh` | Picks CUDA or ROCm automatically; force with `cpu`, `cuda` or `rocm` as the argument. |
| Docker | `cp .env.example .env`, set `ASR_ACCESS_TOKEN`, then `docker compose up -d --build` | The container always listens on `0.0.0.0`, so Docker needs an access token. NVIDIA: also layer `compose.cuda.yaml`; AMD: `compose.rocm.yaml`. See `docs/deployment.md#docker`. |

Generate a token with `uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(32))"` (or `openssl rand -base64 32`) and let the user paste it into `.env` themselves; don't echo it back into the conversation.

## Start and stop

| Way | Start | Stop |
| --- | --- | --- |
| Desktop shortcut / `scripts\start.ps1` (Windows) | Double-click, or run the script. Opens the browser when ready (`-NoBrowser` to skip). Fills in `.env` variables added by newer versions. | Ctrl+C in its window. Closing the window kills the service abruptly. |
| `bash scripts/start.sh` (Linux) | Same as above. | Ctrl+C |
| From an agent | Run in the background: `uv run --directory <install-dir> --no-sync --env-file .env python -m app` | Stop that background task. |
| Docker | `docker compose up -d` | `docker compose stop` (or `docker stop -t 15 <container>`) |

After starting, poll `<asr> status` until it answers (about 15 s on later starts).

If the service refuses to start because `.env` lacks a variable (common right after an update), add the missing ones with their example values. This never changes values the user already set:

```
uv run --directory <install-dir> --no-sync python -m app.envfile .env.example .env
```

Port `7860` in `.env.example` is the convention; if something else holds the port, ask the user before changing `ASR_PORT`.

## First start

- Downloads about 9 GB of model weights from Hugging Face, plus about 330 MB for diarization and punctuation in the background.
- Auto-tunes: measures every device × weight format (int4, int8, fp16, fp32) on this machine and keeps the fastest accurate one. Takes about 7 minutes on a recent laptop and up to ~35 minutes on an 8 GB machine. `status` shows `硬體 tuning` and the current step.
- The default model becomes usable first; the other one is converted afterwards.
- Offline afterwards: weights are cached, and `HF_HUB_OFFLINE=1` keeps it from contacting Hugging Face.

## Update

Ask before running these: `git pull` changes the user's checkout.

| Install | Steps |
| --- | --- |
| Native | Stop the service; `git pull`; `uv sync --frozen` (NVIDIA: `uv sync --frozen --no-default-groups --group cu130`; AMD: `--group rocm72`), or rerun the install script; start again with the start script, which adds new `.env` variables. |
| Docker, built locally | `git pull`, then `docker compose up -d --build` |
| Docker, published image | `docker compose pull`, then `docker compose up -d --no-build` |

Docker skips the start scripts: if the service then refuses to start over a missing variable, run the `app.envfile` command above on the host or copy the line from `.env.example`. Read `CHANGELOG.md` for new settings and behavior changes. If the user edited `models.json`, merge changes from the changelog by hand.

## Errors

The service answers errors as `{"detail": "<reason in Chinese>"}`; the CLI prints `錯誤：<reason>`.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `連不上 Breeze ASR 服務` | Service not running, wrong URL or port | Start it; check `ASR_HOST`/`ASR_PORT` in `.env` |
| `不知道服務的網址` | Ran the CLI without `.env` | Use `--env-file .env` (installed CLI) or `--url` (script) |
| 400 `服務不接受 … 這個名稱` | URL host not in `ASR_ALLOWED_HOSTS` | Use `127.0.0.1` locally, or have the user add the name and restart |
| 400 unsupported model / option | Model id not in `models.json`, or diarization/punctuation not provided by this install | Check `status` |
| 401 `需要存取權杖` / `拒絕這個存取權杖` | Token missing or different from the service's | Set `ASR_ACCESS_TOKEN` to the service's value (never print it) |
| 409 | Job already finished (cancel), no result yet (export, render), or the service is busy (retune, hardware switch) | Wait or check `jobs` |
| 413 `檔案超過上限` | File over `ASR_MAX_UPLOAD_MB` | Compress or split with ffmpeg |
| 422 `檔案裡沒有可辨識的音訊` | Corrupt file or no audio track | Check with `ffprobe`; re-extract audio |
| 422 `音訊長度超過上限` | Over `ASR_MAX_AUDIO_SECONDS` | Split, e.g. `ffmpeg -i in.m4a -f segment -segment_time 3600 -c copy part%02d.m4a` |
| 422 `熱詞太長` | Over 1000 characters or the model's token limit (223 for Breeze ASR) | Shorten to the essential names and terms |
| 422 video length mismatch (render) | Not the original video | Use the exact video the job came from |
| 503 queue full | `ASR_MAX_QUEUE` jobs waiting | Wait for jobs to finish |
| Job `failed` with CUDA out of memory | GPU memory used by something else | User can pick a CPU profile under 硬體 in the web app, or retune when the GPU is free |
| `speakers_error` / `punctuation_error` in the result | That step failed; the transcript is still there | Report it; check `inference.log` / `breeze-asr.log` |

## Logs

In `<ASR_DATA_DIR>/logs/` (`data/logs/` by default): `breeze-asr.log` (web process: requests, jobs, stages, rejected requests), `inference.log` (model download, tuning, transcription), `*-crash.log` and `inference-native.log` for crashes. Logs record file names and job ids, not transcript text, hotwords or names. Docker: `docker compose logs`.

## Other devices

Opening the service to other computers, phones or tablets requires an access token, adding each host name to `ASR_ALLOWED_HOSTS`, and preferably HTTPS via `tailscale serve`; plain HTTP on a LAN sends audio, transcripts and the token in clear text. Walk the user through `docs/deployment.md` sections 讓其他電腦連線 and 從手機或平板使用 rather than editing `.env` yourself, and never set `ASR_HOST=0.0.0.0` without a token and the user's explicit request.

## Watched folder

For recurring batches, `ASR_WATCH_DIR` in `.env` makes the service transcribe every recording dropped into that folder and write results next to it (model, speakers, punctuation and formats from the `ASR_WATCH_*` variables). Changing it needs a restart; see `docs/deployment.md#監看資料夾`.
