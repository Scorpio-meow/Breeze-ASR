# Changelog

格式依照 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號依照 [語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

## [0.2.3] - 2026-11-01

### Added

- 安裝與啟動腳本會把 `.env` 缺少的變數依 `.env.example` 的預設值補上，並列出補了哪些；已設定的值不會被改。從舊版更新時不必再手動對照 `.env.example`（Docker 版見部署文件）。

### Changed

- Windows 的 `scripts\start.ps1`（桌面捷徑）啟動失敗或服務異常結束時，視窗會停住並顯示原因，按 Enter 才關閉，不再一閃就消失。

## [0.2.2] - 2026-11-01

### Added

- 必填的 `ASR_LOG_LEVEL`、`ASR_LOG_FILE_MB`、`ASR_LOG_FILE_COUNT`：日誌等級與換檔大小、保留份數（原本固定 INFO、5 MB、5 份）。升級時請把 `.env.example` 的這三行加進 `.env`。
- 日誌補上：啟動時的版本與生效設定；工作每個階段的開始、處理中每 5 分鐘一行目前的階段與耗時、有回報進度時每跨過 10% 的進度、推論子行程的辨識開始與完成；載入模型、辨識開始與完成、推論子行程意外結束時的記憶體用量；分辨說話者合併前的群數、略過的講話秒數與語者庫比對結果；被拒絕的請求與原因（欄位格式不符時只記欄位名稱）；ffmpeg 解碼時回報的錯誤；燒錄字幕的開始、取消、清除與成功時的警告；服務關閉或終端機視窗被關閉時還沒完成的工作；子行程的錯誤輸出；執行緒與行程未處理的例外，包括推論子行程啟動時的例外；暫存資料夾刪不掉、nvidia-smi 執行失敗。
- `breeze-asr-crash.log`、`inference-crash.log`：原生程式碼當掉時的呼叫堆疊；`inference-native.log`：推論子行程裡原生函式庫印出的訊息。
- `POST /api/client-errors`：網頁把未處理的 JavaScript 錯誤與上傳時的連線中斷回報到日誌；移除控制字元，整個服務每分鐘最多記 30 則。

### Changed

- 日誌每行的時間改成含時區（`2026-10-31 23:36:35.503+08:00`），並加上行程編號（`[推論 24884]`）。
- 啟動時先綁定連接埠再啟動服務；連接埠被占用時直接結束，不再先啟動推論子行程、載入模型。`ASR_HOST` 是主機名稱（例如 `localhost`）時，照樣同時聽 IPv4 與 IPv6。
- 讀不出來的保存結果會改名為 `.bad`，不再每次啟動都重新讀一次。
- `compose.yaml` 設定 `stop_grace_period: 15s`，停止容器時有足夠時間等辨識中的推論子行程結束，不會在關閉途中被強制結束。

### Fixed

- 切換硬體設定時若寫入設定檔失敗，推論子行程會改用原本的設定重新啟動，不再停著等到被當成意外結束。
- 辨識佇列、燒錄佇列與推論事件的處理遇到未預期的錯誤時，不再整個停住、讓之後的工作永遠卡在處理中。
- 遠端服務的回應不是 JSON 時，只讓那個模型標成無法使用，不再讓其他模型永遠停在準備中。
- 燒錄字幕時 ffmpeg 輸出大量錯誤訊息不會再卡住（Linux／Docker）。
- 按 Ctrl+C 關閉時，推論子行程不會先被中斷，不再誤記成「推論行程意外結束」。
- 自動標點的輸出無法解析或段數不符時，只保留沒有標點的文字，不再讓整個工作失敗。
- 監看資料夾寫出結果失敗時，錯誤不會在同一次檢查就被清掉。

## [0.2.1] - 2026-10-31

### Changed

- 自動調校選定裝置後，同一裝置上若有別的格式速度相差 5% 以內、執行時記憶體較少，改用記憶體最少的那一個，選擇理由多一行「改用…」。已經寫好的硬體設定檔不受影響，要套用請重新調校。

## [0.2.0] - 2026-10-31

### Added

- 「工作」清單與 `GET /api/jobs`：列出保留中的工作，點一下開啟；重新整理後自動開啟上次那一份。從清單開啟的舊結果可以按「對應原始檔…」後再播放與對時。
- 字幕編輯：右側「字幕」面板改時間（可設為播放位置）、改說話者或新增說話者、分割（文字游標或播放位置，Ctrl+Enter）、與下一段合併、刪除；「說話者」面板可把一位說話者併入另一位；尋找取代（Ctrl+F）；復原與重做（Ctrl+Z、Ctrl+Y）。後端新增 `PUT /api/jobs/{id}/transcript`，以版本號避免不同分頁互相覆蓋。
- 熱詞：在「詞彙」設定後勾選「熱詞」，OpenVINO 以 `hotwords`、PyTorch 以 `prompt_ids` 帶入每個解碼視窗；送出前檢查 token 上限（223）。實測見效能實測的「熱詞」。
- 詞彙表的取代規則：自動套用到新的辨識結果，也能套用到現有字幕；`GET`／`PUT /api/glossary`。
- 自動標點：sherpa-onnx 的 CT-Transformer 中英標點模型，逐字對齊後只把標點放回原文；`models.json` 新增必填的 `punctuation` 欄位（不提供設為 `null`）。
- 匯出 ASS、Markdown、DOCX；燒錄字幕到原始影片（`POST /api/jobs/{id}/renders`），字型由新的 `ASR_SUBTITLE_FONT`、`ASR_SUBTITLE_FONTS_DIR` 設定，Docker 映像檔內建 Noto Sans CJK。
- 影片在右側預覽並疊上目前的字幕。
- 一次選多個檔案排隊辨識。
- 監看資料夾：新的 `ASR_WATCH_*` 設定（都必須填寫，不監看時 `ASR_WATCH_DIR=off`），Docker 用 `compose.watch.yaml` 掛入主機資料夾。
- 命令列工具 `python -m app.cli` 與 MCP server `python -m app.mcp_server`。
- 日誌寫進 `ASR_DATA_DIR/logs/`（網頁行程 `breeze-asr.log`、推論子行程 `inference.log`，滿 5 MB 換新、保留 5 個）；啟動時記下功能摘要，並記錄字幕編輯與版本衝突、詞彙表、語者庫、監看資料夾與 MCP 的操作，工作完成時加上段數與說話者數。熱詞計數的請求不記入存取日誌。

### Changed

- `POST /api/jobs` 新增必填的 `punctuation` 欄位與選填的 `hotwords` 欄位。
- `.env` 新增必填的 `ASR_SUBTITLE_FONT`、`ASR_SUBTITLE_FONTS_DIR` 與 `ASR_WATCH_*`；舊的 `.env` 要照 `.env.example` 補上，否則服務拒絕啟動。
- `.env.example` 的註解精簡為每區一行，各變數的說明集中在 `docs/configuration.md`。

## [0.1.0] - 2026-10-29

第一個版本。

### Added

- 網頁 APP：上傳音訊或影片、拖進視窗或瀏覽器錄音，工作排隊並在狀態列顯示排隊位置、階段與預估時間。
- 字幕編輯軟體式的介面：工具列、波形與時間尺、說話者軌道、字幕列表、右側面板與狀態列；時間軸可點擊或拖曳定位，空白鍵播放／暫停，播放時字幕列表跟著捲動。
- 支援 Breeze ASR 25 與 Breeze ASR 26，模型清單由 `models.json` 指定並固定版本。
- 三種推論方式：本機 OpenVINO、本機 PyTorch、轉呼叫既有的 Breeze ASR 服務。
- 自動調校：第一次啟動時依 OpenVINO 裝置回報的運算精度，實測各裝置與權重格式（int4、int8、fp16、fp32），選出最快且輸出正確的推論設定；只轉換要實測的格式，放不下的格式在轉換前就略過；Intel NPU 實測無法執行，不納入。可重新調校、在「硬體」改用其他實測過的設定，或手寫硬體設定檔。
- 排隊中與處理中的工作都能取消；工作進行中不能重複送出同一段音訊。
- 瀏覽器從其他網站送來的寫入請求一律拒絕。
- 日誌記錄下載、轉換、調校、工作與分辨說話者的開始、完成與耗時。
- Breeze ASR 26 的句子分段：用逐字時間戳記把 30 秒長段落切成句子級字幕。
- 分辨說話者（自動或指定人數）與語者庫，可替說話者取名並自動認人。
- 匯出 TXT、SRT、VTT、JSON；結果儲存到資料目錄，重新啟動後仍可下載。
- Windows 原生執行與 Docker 部署（CPU、NVIDIA GPU、限定 CPU 核心、加入既有 Docker 網路）。
- 單元測試（pytest）、ruff 檢查與格式設定、GitHub Actions CI。
- 安裝腳本：Windows（`scripts/install.ps1`，含桌面捷徑）與 Linux（`scripts/install.sh`），自動偵測顯示卡。
- AMD GPU（ROCm 7.2，僅 Linux）支援，與 NVIDIA CUDA 一樣由自動調校實測。
- 句子分段改為 OpenVINO 與 PyTorch（CUDA、ROCm）都支援，使用預設模型清單時 NVIDIA、AMD GPU 也會納入自動調校。
- 發布 CPU、CUDA、ROCm 三種 Docker 映像檔到 GitHub Container Registry。
- 社群文件：行為準則、無障礙聲明、issue 範本（問題回報、功能建議、無障礙問題）與 Pull Request 範本。
- 無障礙：所有文字顏色在實際背景上達到 4.5:1 以上的對比；正在播放的字幕列不只靠顏色標示；時間軸是可用方向鍵操作的滑桿；文字大小跟隨瀏覽器的字型大小設定；按鈕與輸入框的點擊範圍至少 24×24 像素。
- 字幕文字可以直接在列表上修改，全文與下載的檔案跟著更新。
- 編譯快取依轉換後的模型分資料夾存放；推論子行程每次啟動都會刪掉對應模型已不存在的快取，快取不再無限增長。
- 超過 30 分鐘或瀏覽器無法解碼的音訊，送出辨識後由伺服器產生波形。
- 在不能錄音的網址按「錄音」，狀態列說明原因與瀏覽器的設定方式。
- 所有回應加上 `Cache-Control: no-cache`，更新版本後瀏覽器不會繼續使用舊的前端程式。

[Unreleased]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Scorpio-meow/Breeze-ASR/releases/tag/v0.1.0
