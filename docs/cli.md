# 命令列工具與 MCP server

[README](../README.md) · [部署](deployment.md) · [設定](configuration.md) · [運作方式](how-it-works.md) · [API](api.md) · **命令列與 MCP** · [效能實測](benchmarks.md)

兩者都是呼叫執行中的服務（先用 `scripts\start.ps1` 或桌面捷徑啟動），檔案從這台電腦上傳到服務，不經過其他地方。服務網址取自 `.env` 的 `ASR_HOST` 與 `ASR_PORT`（`0.0.0.0` 會改連 `127.0.0.1`），所以要在專案資料夾用 `uv run --env-file .env` 執行；命令列工具也可以用 `--url` 指定。

## 命令列工具

```powershell
uv run --no-sync --env-file .env python -m app.cli transcribe 週會.m4a 訪談.mp4 --speakers auto --punctuation on --formats md,srt
```

| 指令 | 說明 |
| --- | --- |
| `transcribe 檔案…` | 送出一個或多個檔案（全部先排隊，再依序等結果），完成後寫出結果，並印出寫出的路徑 |
| `jobs` | 列出服務裡保留中的工作：id、狀態、檔名、長度、段數 |
| `export 工作id --format srt [--output 檔案]` | 下載某個工作的結果；沒給 `--output` 時輸出到標準輸出 |

`transcribe` 的參數：

| 參數 | 必填 | 說明 |
| --- | --- | --- |
| `--speakers` | 是 | `off`、`auto` 或人數 |
| `--punctuation` | 是 | `on` 或 `off` |
| `--formats` | 是 | 以逗號分隔：`txt`、`srt`、`vtt`、`ass`、`md`、`docx`、`json` |
| `--model` | 否 | `models.json` 的模型 id；沒給時用服務的預設模型 |
| `--hotwords 文字` | 否 | 這次辨識的熱詞 |
| `--glossary` | 否 | 改用網頁「詞彙」裡的熱詞（和 `--hotwords` 擇一） |
| `--out 資料夾` | 否 | 結果寫到這個資料夾；沒給時寫在原檔旁邊。檔名是原檔名加格式，例如 `週會.m4a.md` |

進度印在標準錯誤；有任何檔案失敗時結束碼是 1。詞彙表的取代規則由服務套用，命令列送出的工作也會套用。

## MCP server

給 Claude Code 等 MCP 用戶端直接呼叫，只走 stdio。依照 MCP [`2026-07-28`](https://modelcontextprotocol.io/specification/2026-07-28) 規格實作（無狀態，用戶端以 `server/discover` 取得版本與功能），也接受用 `initialize` 握手的舊版用戶端（`2024-11-05` 到 `2025-11-25`）。在 Claude Code 加入（路徑換成專案資料夾）：

```powershell
claude mcp add breeze-asr -- uv run --directory C:/code/Breeze-ASR --no-sync --env-file .env python -m app.mcp_server
```

server 回報的名稱是 `breeze-asr`，標題 `Breeze ASR`，版本與網址取自 `pyproject.toml`，圖示是 `static/icon.svg`。

### 工具

| 工具 | 標題 | 說明 |
| --- | --- | --- |
| `list_models` | 可用的模型 | 模型的 id、名稱、準備狀態、是否支援熱詞，以及服務能不能分辨說話者（最多幾人）、自動加標點 |
| `transcribe` | 辨識錄音 | 辨識這台電腦上的檔案，參數見下表 |
| `job_status` | 工作進度 | 狀態與進度；完成後附上說話者清單（編號、名字、講話秒數）與 Markdown 逐字稿的資源 URI |
| `get_transcript` | 取得逐字稿 | 結果全文：`md`、`txt`、`srt`、`vtt`、`ass` 或 `json` |
| `list_jobs` | 工作清單 | 服務裡保留中的工作，最新的在前 |
| `cancel_job` | 取消工作 | 取消排隊中或處理中的工作；處理中的工作要等推論停下，回傳時可能仍是 `running` |
| `rename_speaker` | 替說話者改名 | 用 `job_status` 的說話者編號（例如 `S1`）改名，會解除這位說話者和語者庫的對應 |

`transcribe` 的參數：

| 參數 | 必填 | 說明 |
| --- | --- | --- |
| `path` | 是 | 檔案在這台電腦上的完整路徑 |
| `speakers` | 是 | `off`、`auto` 或人數 |
| `punctuation` | 是 | 是否自動加標點 |
| `wait` | 是 | `true` 等辨識完成並回傳 Markdown 逐字稿；長錄音請用 `false`，再用 `job_status` 查進度 |
| `model` | 否 | `list_models` 的模型 id；沒給時用服務的預設模型 |
| `hotwords` | 否 | 這次辨識的熱詞 |
| `use_glossary` | 否 | 改用網頁「詞彙」裡的熱詞（和 `hotwords` 擇一） |

`wait` 為 `true` 時，server 每 2 秒查一次進度；用戶端有要求進度通知時，每次都送出 `notifications/progress`，`progress` 是已等待的秒數，`message` 是進度說明（例如「辨識中 42%」）。用戶端取消這個呼叫不會停止辨識，要停止請用 `cancel_job`。

每個工具都帶有 `title` 與 annotations，用戶端可以依此決定呼叫前要不要先問使用者。所有工具都只連這台電腦的服務，`openWorldHint` 都是 `false`：

| 工具 | `readOnlyHint` | `destructiveHint` | `idempotentHint` |
| --- | --- | --- | --- |
| `list_models`、`job_status`、`get_transcript`、`list_jobs` | `true` | `false` | `true` |
| `transcribe` | `false` | `false` | `false` |
| `cancel_job`、`rename_speaker` | `false` | `true` | `true` |

除了 `get_transcript` 直接回傳文字，其他工具都有 `outputSchema`，結果放在 `structuredContent`，同時附上相同內容的 JSON 文字。工具出錯時（找不到檔案、工作已過期、連不上服務）回傳 `isError: true` 與原因。

### 資源

| URI | 類型 | 內容 |
| --- | --- | --- |
| `breeze-asr://glossary` | `application/json` | 網頁「詞彙」裡的熱詞與取代規則 |
| `breeze-asr://jobs/{job_id}/transcript.md` | `text/markdown` | 含時間戳與說話者的逐字稿 |
| `breeze-asr://jobs/{job_id}/transcript.txt` | `text/plain` | 純文字 |
| `breeze-asr://jobs/{job_id}/transcript.srt` | `application/x-subrip` | SRT 字幕 |
| `breeze-asr://jobs/{job_id}/transcript.vtt` | `text/vtt` | WebVTT 字幕 |
| `breeze-asr://jobs/{job_id}/transcript.ass` | `text/x-ssa` | ASS 字幕 |
| `breeze-asr://jobs/{job_id}/transcript.json` | `application/json` | 每段的時間、文字、說話者編號，以及說話者清單 |

逐字稿資源每次讀取都向服務重新取得，網頁上的修改會立即反映。工作不存在或已過期時回傳 JSON-RPC 錯誤 `-32602`，連不上服務是 `-32603`。

### 提示

| 提示 | 標題 | 參數 | 內容 |
| --- | --- | --- | --- |
| `meeting_minutes` | 整理會議紀錄 | `job_id` | 摘要、討論重點、決議與待辦事項（負責人、期限） |
| `proofread_transcript` | 校對逐字稿 | `job_id` | 可能辨識錯的字詞，以及可以貼到網頁「詞彙」的熱詞與取代規則 |

兩個提示都把 Markdown 逐字稿當成內嵌資源放在第一則訊息，第二則是整理的要求。提示與逐字稿資源的 `job_id` 可以自動補全，只列出已完成的工作。

> [!CAUTION]
> 逐字稿會進到 MCP 用戶端的對話裡，可能因此送到外部 AI 服務。內容不能交給外部 AI 的錄音，只用網頁或命令列工具。

MCP server 的日誌寫到標準錯誤，由 MCP 用戶端保留；服務本身的日誌見[部署](deployment.md#日誌)。
