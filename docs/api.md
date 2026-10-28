# API

網頁用的就是這組 API。所有路徑都在 `/api` 底下，錯誤回應是 `{"detail": "原因"}`。OpenAPI 規格在 `/api/openapi.json`。

瀏覽器從其他網站送來的 `POST`、`PATCH`、`DELETE`（`Sec-Fetch-Site` 是 `cross-site` 或 `same-site`）一律回 403；curl 等不帶這個標頭的程式不受影響。

```bash
curl -F model=MediaTek-Research/Breeze-ASR-25 -F speakers=auto -F file=@meeting.m4a http://127.0.0.1:7860/api/jobs
curl http://127.0.0.1:7860/api/jobs/<id>
curl -o meeting.srt http://127.0.0.1:7860/api/jobs/<id>/export/srt
```

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| `GET` | [`/api/health`](#get-apihealth) | 服務是否在線 |
| `GET` | [`/api/config`](#get-apiconfig) | 模型、硬體、分辨說話者的狀態與上限設定 |
| `POST` | [`/api/hardware/retune`](#post-apihardwareretune) | 重新調校 |
| `POST` | [`/api/hardware/settings`](#post-apihardwaresettings) | 改用另一個實測過的設定 |
| `POST` | [`/api/jobs`](#post-apijobs) | 建立辨識工作 |
| `GET` | [`/api/jobs/{id}`](#get-apijobsid) | 查詢工作狀態與結果 |
| `DELETE` | [`/api/jobs/{id}`](#delete-apijobsid) | 取消排隊中或處理中的工作 |
| `PATCH` | [`/api/jobs/{id}/speakers/{speaker_id}`](#patch-apijobsidspeakersspeaker_id) | 替說話者改名 |
| `PATCH` | [`/api/jobs/{id}/segments/{index}`](#patch-apijobsidsegmentsindex) | 修改一段字幕的文字 |
| `GET` | [`/api/jobs/{id}/export/{fmt}`](#get-apijobsidexportfmt) | 下載結果 |
| `GET` | [`/api/voices`](#get-apivoices) | 語者庫清單 |
| `POST` | [`/api/voices`](#post-apivoices) | 記住說話者的聲音 |
| `DELETE` | [`/api/voices/{id}`](#delete-apivoicesid) | 從語者庫刪除 |

## GET /api/health

**回應：** 200 `{"status": "ok"}`

## GET /api/config

**回應：** 200

| 欄位 | 說明 |
| --- | --- |
| `default_model` | 預設模型的 id |
| `models` | 每個模型的 `id`、`name`、`tagline`、`description`、`backend`（`local`／`remote`）、`state`、`error`、`seconds_per_audio_second` |
| `hardware` | 調校狀態（`state`：`tuning`、`ready`、`failed`、`not_needed`）、使用中的設定與實測紀錄 |
| `speakers` | 分辨說話者模型的狀態、`max_speakers`、`max_name_length`；沒有提供這個功能時是 `null` |
| `max_upload_mb`、`max_audio_seconds` | 上傳上限 |

模型的 `state`：`waiting`、`downloading`、`converting`、`prepared`、`loading`、`ready`、`failed`。

## POST /api/hardware/retune

重新偵測硬體，把預設模型重新轉成要實測的權重格式並實測（i7-9700 上約 7–9 分鐘），期間無法辨識。

**回應：**
- 202：新的調校狀態
- 409：還有工作在排隊或處理中，或正在調校中

## POST /api/hardware/settings

改用實測紀錄裡的另一個設定，`application/json`：`{"label": "Intel(R) UHD Graphics 630 (iGPU)（OpenVINO GPU・int8）"}`，`label` 取自 `GET /api/config` 的 `hardware.tuning.candidates[].label`。只能選實測時跑得起來的項目（錯誤率不合格的也可以選）；會依該項目量到的記憶體重新決定常駐模型數，寫回硬體設定檔，再重新啟動推論子行程。該格式已被調校清掉時，第一次使用會重新轉換。

**回應：**
- 202：新的硬體狀態
- 409：還有工作在排隊或處理中，或推論設定還沒就緒
- 422：設定檔沒有實測紀錄、找不到這個設定，或它實測時無法執行

## POST /api/jobs

建立辨識工作，`multipart/form-data`。

| 欄位 | 必填 | 說明 |
| --- | --- | --- |
| `model` | 是 | `models.json` 裡的模型 id |
| `speakers` | 是 | `off`（不分辨）、`auto`（自動判斷人數）或 2–`max_speakers` 的人數 |
| `file` | 是 | 音訊或影片檔 |

**回應：**
- 202：工作狀態（格式同 [`GET /api/jobs/{id}`](#get-apijobsid)），另外多一個 `waveform`：把整段音訊等分成 4000 格、每格的峰值（0–1），網頁在瀏覽器無法自行解碼時用它畫波形
- 400：不支援的模型，或 `speakers` 不合法
- 411：請求缺少 `Content-Length`
- 413：檔案超過 `ASR_MAX_UPLOAD_MB`
- 422：無法解碼，或音訊超過 `ASR_MAX_AUDIO_SECONDS`
- 503：排隊的工作已達 `ASR_MAX_QUEUE`

## GET /api/jobs/{id}

**回應：** 200，或 404（找不到或已過期）

| 欄位 | 說明 |
| --- | --- |
| `id`、`model`、`filename`、`audio_seconds` | 工作的基本資料 |
| `status` | `queued`、`running`、`succeeded`、`failed`、`cancelled` |
| `stage` | 處理中的階段：`preparing`、`loading`、`transcribing`、`diarizing` |
| `speakers_mode` | 建立時的 `speakers` |
| `created_at`、`finished_at` | Unix 時間 |
| `error` | 失敗原因 |
| `queue_position`、`jobs_ahead` | 只在排隊中出現 |
| `stage_elapsed_seconds` | 辨識或分辨說話者階段已經過的秒數 |
| `progress`、`estimated_seconds` | 辨識進度（0–1，OpenVINO 不提供）與預估總秒數（依自動調校實測與先前工作的平均速度），無法得知時為 `null` |
| `speakers_progress` | 分辨說話者的進度（0–1） |
| `result` | 完成後才有，見下方 |

`result`：

```json
{
  "text": "完整文字",
  "segments": [{ "start": 0.0, "end": 4.24, "text": "那顯然這樣子的 attention 是有問題的", "speaker": "S1" }],
  "speakers": [{ "id": "S1", "name": "說話者 1", "seconds": 26.0, "voice_id": null, "similarity": null }],
  "speakers_error": null,
  "audio_seconds": 29.11,
  "processing_seconds": 18.4
}
```

不分辨說話者時 `speaker` 與 `speakers` 是 `null`；分辨說話者失敗時 `speakers_error` 說明原因，字幕照常產生。

## DELETE /api/jobs/{id}

排隊中的工作立即取消。處理中的工作會結束推論子行程再重新啟動，狀態稍後變成 `cancelled`。

**回應：**
- 200：工作狀態
- 404：找不到工作
- 409：工作已經結束

## PATCH /api/jobs/{id}/speakers/{speaker_id}

替說話者改名，JSON：`{"name": "王小明"}`，最多 40 字。

**回應：** 200 更新後的工作狀態，或 404（找不到工作或說話者）

## PATCH /api/jobs/{id}/segments/{index}

修改第 `index` 段（從 0 起算）的文字，JSON：`{"text": "修正後的字幕"}`。連續空白與換行會合併成一個空格，最多 500 字；時間不能改。結果的 `text` 與之後下載的檔案都會用修改後的文字。

**回應：** 200 更新後的工作狀態、404（找不到工作或這一段），或 422（空白或超過 500 字）

## GET /api/jobs/{id}/export/{fmt}

`fmt` 是 `txt`、`srt`、`vtt` 或 `json`，以附件下載，檔名沿用上傳的檔名。

**回應：**
- 200：檔案內容
- 404：找不到工作，或不支援的格式
- 409：工作還沒有辨識結果

## GET /api/voices

**回應：** 200 `{"voices": [...]}`，每筆有 `id`、`name`、`samples`、`outdated`、`created_at`、`updated_at`，不含聲紋本身。沒有提供分辨說話者時回 404。

## POST /api/voices

把某個結果裡的說話者以目前的名字記進語者庫。同名的人會累加聲紋。

```json
{ "job_id": "…", "speaker_id": "S1" }
```

**回應：**
- 201：`{"voice": {"id", "name", "samples"}, "job": 更新後的工作狀態}`
- 404：找不到工作或說話者
- 422：說話者還是預設名字，請先取名

## DELETE /api/voices/{id}

**回應：** 200 `{"voices": [...]}` 刪除後的清單，或 404（語者庫裡沒有這個人）
