# 命令列工具與 MCP server

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

給 Claude Code 等 MCP 用戶端直接呼叫。在 Claude Code 加入（路徑換成專案資料夾）：

```powershell
claude mcp add breeze-asr -- uv run --directory C:/code/Breeze-ASR --no-sync --env-file .env python -m app.mcp_server
```

| 工具 | 說明 |
| --- | --- |
| `list_models` | 可用的模型與功能（分辨說話者、自動標點、熱詞） |
| `transcribe` | 辨識這台電腦上的檔案：`path`、`speakers`、`punctuation`、`wait` 必填，`model`、`hotwords` 可省略。`wait` 為 `true` 時等完成並回傳 Markdown 逐字稿；長錄音請用 `false`，再用 `job_status` 查進度 |
| `job_status` | 工作的狀態與進度 |
| `get_transcript` | 取得結果：`md`、`txt`、`srt`、`vtt`、`ass` 或 `json` |
| `list_jobs` | 服務裡保留中的工作 |

逐字稿會進到 MCP 用戶端的對話裡；內容不能交給外部 AI 的錄音，只用網頁或命令列工具。MCP server 的日誌寫到標準錯誤，由 MCP 用戶端保存；服務本身的日誌見[部署](deployment.md#日誌)。
