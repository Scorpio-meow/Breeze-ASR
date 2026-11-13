# HTTP API

Use the API only for what `<asr>` does not cover: editing segment text or timing, counting hotword tokens, and (when the user asks) retuning or switching the hardware profile. The full, machine-readable spec is `docs/openapi.yaml` in the install folder or <https://raw.githubusercontent.com/Scorpio-meow/ListenMiao/main/docs/openapi.yaml>; it is long, so search it for the operation you need instead of reading it whole.

All paths are under `/api` on the service URL. Errors come back as `{"detail": "<reason>"}` (422 validation errors: `detail` is a list).

## Authentication and host names

- When the service has an access token, send `Authorization: Bearer $ASR_ACCESS_TOKEN` on every request except `GET /api/health`. Reference the environment variable; never paste the token into a command line you show the user.
- The `Host` you connect with must be in `ASR_ALLOWED_HOSTS`, otherwise every request gets 400. Locally use `127.0.0.1` or `localhost`.

## Non-ASCII request bodies on Windows

Windows PowerShell 5.1 passes arguments to `curl.exe` in the ANSI code page, so Chinese text inside `-d '{...}'` reaches the service garbled. Write the JSON to a UTF-8 file and send the file:

```
curl -X PATCH -H "Content-Type: application/json; charset=utf-8" --data-binary @body.json http://127.0.0.1:7860/api/jobs/<id>/segments/3
```

Git Bash, Linux and macOS shells send UTF-8 and are fine. Prefer `<asr>` whenever it has a command for the job; it handles encoding itself.

## Endpoints

| Method and path | Use |
| --- | --- |
| `GET /api/health` | `{"status": "ok"}` when the service is up |
| `GET /api/config` | Models and their state, hardware profile, diarization and punctuation availability, export formats, limits |
| `POST /api/hotwords/count` | `{"model": "<id>", "text": "<hotwords>"}` → `{"tokens": 15, "limit": 223}`; check long hotwords before submitting |
| `GET /api/jobs` / `GET /api/jobs/{id}` | Job list / one job with `status`, `stage`, `progress`, `estimated_seconds` and, when done, `result` |
| `POST /api/jobs` | multipart: `model`, `speakers` (`off`/`auto`/count), `punctuation` (`on`/`off`), optional `hotwords`, `file` → 202 with the job |
| `DELETE /api/jobs/{id}` | Cancel a queued or running job |
| `GET /api/jobs/{id}/export/{fmt}` | `txt`, `srt`, `vtt`, `ass`, `md`, `docx`, `json` |
| `PATCH /api/jobs/{id}/segments/{index}` | `{"text": "..."}`: replace the text of one segment (0-based); timing unchanged; 1–500 characters |
| `PUT /api/jobs/{id}/transcript` | Replace all segments and speakers; see below |
| `PATCH /api/jobs/{id}/speakers/{speaker_id}` | `{"name": "..."}` (≤ 40 characters) |
| `POST /api/jobs/{id}/renders`, `GET /api/renders/{id}`, `GET /api/renders/{id}/file`, `DELETE /api/renders/{id}` | Burn-in: upload the original video as multipart `file`, poll, download, cancel |
| `GET`/`PUT /api/glossary` | `{"hotwords": "...", "replacements": [{"find": "...", "replace": "..."}]}`; `PUT` replaces the whole glossary |
| `GET`/`POST /api/voices`, `DELETE /api/voices/{id}` | Voice library; `POST` body `{"job_id": "...", "speaker_id": "S1"}` |
| `POST /api/hardware/retune` | Re-detect hardware and re-tune (7–35 min, no transcription meanwhile). Only on the user's request. |
| `POST /api/hardware/settings` | `{"label": "<hardware.tuning.candidates[].label from /api/config>"}`: switch to another measured profile. Only on the user's request. |

## Editing a whole transcript

`PUT /api/jobs/{id}/transcript` carries the full result back, guarded by a revision number so it can't overwrite edits made elsewhere:

1. `GET /api/jobs/{id}` and read `result.revision`, `result.segments` (`start`, `end`, `text`, `speaker`) and `result.speakers` (`id`, `name`, `voice_id`, `similarity`).
2. Change what you need: fix text, move a segment to another speaker, merge or split segments (times in seconds, two decimals, within the audio length, sorted by `start`).
3. Send `{"revision": <same number>, "segments": [...], "speakers": [...]}`. 409 means someone edited it meanwhile: fetch again and redo the change.

Speakers with no remaining segments are removed. A new speaker id (`S5`) has no voiceprint, so it can't be remembered in the voice library; don't reuse ids from `result.voiceprint_speakers`. For transcripts without diarization, `speakers` and every `speaker` are `null`.

Only edit a transcript when the user asked for that change; the web editor is the better tool for proofreading by hand.
