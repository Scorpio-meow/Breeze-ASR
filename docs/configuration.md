# 設定

[README](../README.md) · [部署](deployment.md) · **設定** · [運作方式](how-it-works.md) · [API](api.md) · [命令列與 MCP](cli.md) · [效能實測](benchmarks.md)

服務的行為由三個檔案決定：環境設定 `.env`、模型清單 `models.json`，以及自動調校寫出的硬體設定檔。

**本頁**：[`.env`](#env) · [接受的主機名稱](#接受的主機名稱) · [存取權杖](#存取權杖) · [`models.json`](#modelsjson) · [`local`](#local) · [`remote`](#remote) · [`speakers`](#speakers) · [`punctuation`](#punctuation) · [硬體設定檔](#硬體設定檔)

## `.env`

> [!IMPORTANT]
> 所有設定都必須填寫，缺少或格式錯誤時服務會直接拒絕啟動並說明原因；程式裡沒有預設值。

最右欄是 `.env.example` 的值。

| 變數 | 說明 | `.env.example` |
| --- | --- | --- |
| `ASR_HOST`、`ASR_PORT` | 原生執行的監聽位址與埠（Docker 版的 `ASR_PORT` 是容器內的埠） | `127.0.0.1`、`7860` |
| `ASR_ALLOWED_HOSTS` | [接受的主機名稱](#接受的主機名稱)，以逗號分隔；必須包含 `ASR_HOST` 對應的本機位址 | `127.0.0.1,localhost` |
| `ASR_ACCESS_TOKEN` | [存取權杖](#存取權杖)，至少 24 個字元；只有 `ASR_HOST` 是本機位址時可以填 `off` | `off` |
| `ASR_MODELS_FILE` | 模型清單檔 | `models.json` |
| `ASR_DATA_DIR` | 轉換後模型、編譯快取、辨識結果、語者庫、詞彙表、日誌與上傳暫存的目錄 | `data` |
| `ASR_HARDWARE_PROFILE` | [硬體設定檔](#硬體設定檔)；不存在時第一次啟動自動調校並寫出 | `data/hardware-profile.json` |
| `ASR_MAX_UPLOAD_MB` | 單檔大小上限 | `2048` |
| `ASR_MAX_AUDIO_SECONDS` | 音訊長度上限 | `10800` |
| `ASR_MAX_QUEUE` | 最多可排隊的工作數 | `20` |
| `ASR_JOB_TTL_SECONDS` | 完成的結果與燒好字幕的影片保留多久，至少 60 | `86400` |
| `ASR_SUBTITLE_FONT` | ASS 匯出與燒錄字幕的字型名稱；含空白時加引號 | `"Microsoft JhengHei"` |
| `ASR_SUBTITLE_FONTS_DIR` | 燒錄字幕時尋找字型檔的資料夾，必須存在 | `C:/Windows/Fonts` |
| `ASR_LOG_LEVEL` | [日誌](deployment.md#日誌)等級：`DEBUG`、`INFO`、`WARNING` 或 `ERROR`；`DEBUG` 也會記下第三方套件的細節 | `INFO` |
| `ASR_LOG_FILE_MB`、`ASR_LOG_FILE_COUNT` | 每個日誌檔滿幾 MB 換新檔、保留幾個舊檔，各至少 1 | `5`、`5` |
| `ASR_WATCH_DIR` | [監看資料夾](deployment.md#監看資料夾)；不監看填 `off` | `off` |
| `ASR_WATCH_MODEL` | 監看資料夾用的模型 id | `MediaTek-Research/Breeze-ASR-25` |
| `ASR_WATCH_SPEAKERS` | 監看資料夾的說話者：`off`、`auto` 或人數 | `auto` |
| `ASR_WATCH_PUNCTUATION` | 監看資料夾是否自動加標點：`on` 或 `off` | `on` |
| `ASR_WATCH_FORMATS` | 監看資料夾寫出的格式，以逗號分隔：`txt`、`srt`、`vtt`、`ass`、`md`、`docx`、`json` | `srt,md,json` |
| `ASR_WATCH_POLL_SECONDS` | 監看資料夾多久檢查一次（秒） | `10` |
| `HF_HUB_DISABLE_TELEMETRY` | 關閉 Hugging Face 用量回報；OpenVINO 與 NNCF 沒有對應的環境變數，由服務自動關閉，見 [SECURITY.md](../SECURITY.md#資料) | `1` |
| `HF_HUB_DISABLE_SYMLINKS_WARNING` | 關閉 Windows 無法建立符號連結時的快取警告（快取改用複製檔案，每個模型只固定一個版本，不會多佔空間） | `1` |

只有 Docker 版會用到：

| 變數 | 說明 | `.env.example` |
| --- | --- | --- |
| `COMPOSE_FILE` | 要套用的 compose 檔 | `compose.yaml` |
| `COMPOSE_PATH_SEPARATOR` | `COMPOSE_FILE` 的分隔符號，疊加多個檔案時設為 `:` | — |
| `ASR_TORCH_GROUP` | 映像檔裡的 PyTorch 版本：`cpu`、`cu130`（CUDA）或 `rocm72`（ROCm） | `cpu` |
| `ASR_IMAGE` | 映像檔名稱；自己建置時任取，使用發布的映像檔時填 `ghcr.io/<owner>/<repo>:cpu` 等 | `breeze-asr-webapp:cpu` |
| `ASR_MODELS_SOURCE` | 掛進容器的模型清單檔 | `./models.json` |
| `ASR_PUBLISH_ADDRESS`、`ASR_PUBLISH_PORT` | 對外發布的位址與埠 | `127.0.0.1`、`7860` |
| `ASR_DOCKER_SUBNET` | Docker 網路網段 | `192.168.202.0/24` |
| `ASR_CUDA_DEVICE_ID` | 交給容器的 GPU 編號，用 `compose.cuda.yaml` 時才需要 | — |
| `ASR_VIDEO_GID`、`ASR_RENDER_GID` | 主機 `video`、`render` 群組的 GID，用 `compose.rocm.yaml` 時才需要 | — |
| `ASR_REMOTE_DOCKER_NETWORK` | 遠端服務所在的 Docker 網路，用 `compose.remote-net.yaml` 時才需要 | — |
| `ASR_WATCH_SOURCE` | 要監看的主機資料夾，用 `compose.watch.yaml` 時才需要 | — |
| `ASR_CPUSET` | 容器可用的 CPU 編號，用 `compose.cpuset.yaml` 時才需要 | — |

各疊加檔怎麼搭配見[部署](deployment.md#docker)。`models.json` 裡 `remote.api_key_env` 指定的變數（例如 `ASR_BREEZE26_SERVICE_API_KEY`）也放在 `.env`。

### 接受的主機名稱

`ASR_ALLOWED_HOSTS` 列出服務接受的主機名稱，也就是網址裡 `http://` 或 `https://` 之後、埠號之前的部分。請求的 `Host` 標頭不在清單裡時，網頁、API 與健康檢查一律回 400，日誌記下被拒絕的名稱。這道檢查用來擋 DNS rebinding，原理見 [SECURITY.md](../SECURITY.md#已有的防護)。

| 連線方式 | 要列入的名稱 |
| --- | --- |
| 這台電腦的瀏覽器、命令列工具、MCP server | `127.0.0.1`、`localhost`（`.env.example` 已列） |
| [`tailscale serve`](deployment.md#從手機或平板使用) | `tailscale serve` 印出的 `<電腦名稱>.<tailnet 名稱>.ts.net` |
| 區網其他電腦用 IP 連線 | 這台電腦的區網 IP，例如 `192.168.1.10` |
| [HTTPS 反向代理](deployment.md#區網自簽憑證) | 手機、平板網址裡的名稱或 IP |
| Docker | 和原生執行相同，列出瀏覽器網址裡的名稱；容器的健康檢查用 `127.0.0.1` |

- 不分大小寫，只填名稱或 IP，不含 `http://` 與埠號；IPv6 寫 `::1` 或 `[::1]` 都可以。沒有 `*` 這類萬用字元，每個名稱都要列出來。
- 同一個名稱從哪個埠連進來都接受，所以 Docker 的 `ASR_PUBLISH_PORT` 和 `ASR_PORT` 不同也不必另外列。
- 清單必須包含 `ASR_HOST` 對應的本機位址（`0.0.0.0` 對應 `127.0.0.1`，`::` 對應 `::1`），否則服務拒絕啟動：命令列工具、MCP server、啟動腳本開的網頁與 Docker 的健康檢查都用這個位址連線。
- 改了清單要重新啟動服務。用沒列入的名稱開啟網頁時，畫面會顯示 `{"detail": "服務不接受 … 這個名稱；…"}`，照提示把名稱加進來即可。
- 區網 IP 變了（例如 DHCP 重新分配）要跟著更新清單，或替這台電腦設定固定 IP。

### 存取權杖

設定 `ASR_ACCESS_TOKEN` 後，網頁、API、命令列工具與 MCP server 都要帶這個權杖才能使用服務。

| `ASR_HOST` | `ASR_ACCESS_TOKEN=off` | 設成權杖 |
| --- | --- | --- |
| 本機位址：`127.0.0.1`、`::1`、`localhost` | 不檢查權杖 | 檢查 |
| 其他位址：`0.0.0.0`、`::`、區網 IP、解析不出來的名稱 | 拒絕啟動 | 檢查 |

- 權杖至少 24 個字元，只能用英文字母、數字與符號，不能有空白。用下面的指令產生隨機字串貼到 `.env`；沒有裝 uv 的電腦（例如只跑 Docker）改用 `openssl rand -base64 32`。

  ```powershell
  uv run --no-sync python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- Docker 版的容器內一律監聽 `0.0.0.0`（`compose.yaml` 固定），所以一定要設定權杖，即使 `ASR_PUBLISH_ADDRESS` 是 `127.0.0.1`。
- 用 `tailscale serve` 或反向代理開放給其他裝置時，`ASR_HOST` 仍是 `127.0.0.1`，服務不會強制要求權杖，請自己設定（見[部署](deployment.md#從手機或平板使用)）。
- 網頁第一次開啟時要輸入權杖，同一個瀏覽器記住 400 天；改掉權杖後所有瀏覽器都要重新輸入。一分鐘內輸錯 10 次，這一分鐘內的網頁登入一律拒絕。
- 命令列工具與 MCP server 從環境變數 `ASR_ACCESS_TOKEN` 讀取權杖，在專案資料夾用 `uv run --env-file .env` 執行時會自動帶上。

## `models.json`

```json
{
  "default_model": "MediaTek-Research/Breeze-ASR-25",
  "models": [
    {
      "id": "MediaTek-Research/Breeze-ASR-25",
      "name": "Breeze ASR 25",
      "tagline": "華語・中英夾雜",
      "description": "…",
      "local": {
        "revision": "cffe7ccb404d025296a00758d0a33468bec3a9d0",
        "language": null,
        "task": "transcribe",
        "sentence_segments": null
      }
    }
  ],
  "speakers": { "…": "見下方說話者設定" },
  "punctuation": { "…": "見下方自動標點設定" }
}
```

| 欄位 | 說明 |
| --- | --- |
| `default_model` | 預設選取的模型，必須是 `models` 裡的 `id` |
| `models[].id` | Hugging Face repo |
| `models[].name`、`tagline`、`description` | 顯示在模型選單與提示 |
| `models[].local` 或 `models[].remote` | 本機推論或轉呼叫遠端服務，每個模型必須剛好一個 |
| `speakers` | 分辨說話者的設定；設為 `null` 時介面不出現相關選項 |
| `punctuation` | 自動標點的設定；設為 `null` 時介面不出現「標點」 |

新增其他 Whisper 架構的模型只要加一筆，工具列的模型選單會自動出現。

### `local`

| 欄位 | 說明 |
| --- | --- |
| `revision` | 固定的 commit，確保每次部署拿到同一份權重；要更新模型時改這裡 |
| `language` | `null` 表示讓模型自動偵測，或填語言代碼（例如 `zh`） |
| `task` | `transcribe` 或 `translate` |
| `sentence_segments` | `null` 表示沿用模型自己輸出的分段；`{"max_seconds": 10, "min_seconds": 1}` 表示用逐字時間戳記重新切成句子級段落，見[句子分段](how-it-works.md#句子分段) |

### `remote`

```json
"remote": {
  "url": "http://breeze-asr-26:9001",
  "api_key_env": "ASR_BREEZE26_SERVICE_API_KEY",
  "fields": { "beam": 3, "context": true },
  "poll_seconds": 1,
  "timeout_seconds": 60,
  "job_timeout_seconds": 7200
}
```

| 欄位 | 說明 |
| --- | --- |
| `url` | 既有 Breeze ASR 服務的網址，需提供 `/v1/transcribe/jobs` 介面 |
| `api_key_env` | 存放 API 金鑰的環境變數名稱；金鑰本身不寫進設定檔 |
| `fields` | 建立工作時一併送出的表單欄位 |
| `poll_seconds`、`timeout_seconds` | 輪詢間隔與單次請求逾時 |
| `job_timeout_seconds` | 一個工作最多等多久；遠端服務卡住時，超過就以錯誤結束，不會擋住後面的工作 |

轉呼叫遠端的模型不支援熱詞；自動標點、取代規則與分辨說話者在本服務執行，照常可用。

### `speakers`

```json
"speakers": {
  "segmentation": {
    "repo": "csukuangfj/sherpa-onnx-pyannote-segmentation-3-0",
    "revision": "9403a6902bb58e3d5ae8c7e77c3422de279db2e0",
    "file": "model.onnx"
  },
  "embedding": {
    "repo": "csukuangfj/speaker-embedding-models",
    "revision": "0743f301363dec56491a490f6d6cbc9d67f9a3bf",
    "file": "3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx"
  },
  "num_threads": 4,
  "cluster_threshold": 0.65,
  "merge_threshold": 0.66,
  "min_speaker_factor": 0.7,
  "match_threshold": 0.7,
  "min_duration_on": 0.3,
  "min_duration_off": 0.5,
  "max_speakers": 8
}
```

各欄位的作用見[分辨說話者](how-it-works.md#分辨說話者)。

| 欄位 | 說明 |
| --- | --- |
| `segmentation`、`embedding` | 分段模型與聲紋模型：Hugging Face repo、固定的 commit、檔名；第一次啟動時在背景下載（共約 45 MB） |
| `num_threads` | 分辨說話者用的 CPU 執行緒數 |
| `cluster_threshold` | 自動判斷人數時第一輪分群的距離門檻，越大群越少 |
| `merge_threshold` | 跨塊合併：兩組的平均連結相似度達到這個值就合併為同一人 |
| `min_speaker_factor` | 說話總長不到「這個係數 × √(全部說話秒數)」的零碎群併入聲紋最像的群 |
| `match_threshold` | 與語者庫聲紋的平均連結相似度達到這個值，才認定是那個人 |
| `min_duration_on`、`min_duration_off` | 短於前者的說話片段捨棄；同一人兩段之間的停頓短於後者就接成一段 |
| `max_speakers` | 介面上可指定的人數上限 |

### `punctuation`

```json
"punctuation": {
  "repo": "csukuangfj/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12",
  "revision": "432aeba669265e7aeb06b9359753419683b38597",
  "file": "model.onnx",
  "num_threads": 4
}
```

| 欄位 | 說明 |
| --- | --- |
| `repo`、`revision`、`file` | sherpa-onnx 標點模型（CT-Transformer）在 Hugging Face 的位置，固定 commit；約 280 MB，啟動時下載 |
| `num_threads` | 加標點用的 CPU 執行緒數 |

## 硬體設定檔

第一次啟動時由[自動調校](how-it-works.md#自動調校)寫出。在工具列的「硬體」按實測表裡某一項的「改用」，會換成那個設定，並重新計算常駐模型數、寫回這個檔案。

也可以手寫固定設定、跳過調校（例如要固定只用 CPU）：

```json
{
  "settings": {
    "engine": "openvino",
    "device": "CPU",
    "weight_format": "int8",
    "torch_dtype": null,
    "torch_num_beams": null,
    "max_loaded_models": 1
  },
  "tuning": null
}
```

| 欄位 | OpenVINO | PyTorch |
| --- | --- | --- |
| `engine` | `openvino` | `torch` |
| `device` | `CPU`、`GPU` 等（不支援 NPU） | `cuda:0` 等 |
| `weight_format` | `fp32`、`fp16`、`int8`、`int4` | `null` |
| `torch_dtype` | `null` | `float16`、`bfloat16`、`float32` |
| `torch_num_beams` | `null` | 1 以上的整數 |
| `max_loaded_models` | 同時常駐的本機模型數 | 同左 |

設定檔有誤（例如欄位錯誤）時服務照常啟動，狀態列顯示原因，本機模型暫時無法使用。
