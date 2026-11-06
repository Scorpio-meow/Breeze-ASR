# Changelog

格式依照 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號依照 [語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

## [0.3.1] - 2026-11-06

### Added

- `docs/benchmarks.md` 補上 Ryzen 9 9950X3D 的 CPU 實測：執行緒數幾乎不影響速度，PyTorch CPU 比 OpenVINO int8 慢 2.3–4.7 倍。
- `docs/benchmarks.md` 補上 Breeze ASR 26 在台語錄音上的量化對照：OpenVINO int8、fp16 和原始 fp32 權重的輸出只差 1.9%、0.9%。
- `docs/benchmarks.md` 補上 RTX 5090 的 CUDA 實測：Breeze ASR 25 速度 0.021，比 RTX 5060 Ti 快約 1.7 倍；卡空著時自動調校選它。

### Fixed

- 分辨說話者沒有偵測到任何人時，結果的 `speakers` 是空陣列、每段的 `speaker` 卻是 `null`，之後的修改一律被拒絕（422）。現在 `speakers` 是 `null`，原因寫在 `speakers_error`；先前存下的這類結果讀回時也一樣轉換。
- 句子分段可能產生開始等於結束的字幕（例如最後一個字的時間超過音訊結尾），之後的修改一律被拒絕。現在只拒絕結束早於開始的字幕，只剩零長度字幕的說話者也會保留。
- 不需要權杖的 `POST /api/session` 會把任何大小的請求整個讀進記憶體；現在超過 4 KB 直接回 413。
- 推論行程當掉後的 5 秒內按「取消」或「重新調校」，會同時跑起兩個推論行程。現在停止與重新啟動一次只做一件。
- 命令列一次轉多個檔案時，只要一個檔案送不出去就整批中止，已送出的檔案也不會寫出結果；現在印出原因，其餘照常辨識。
- 結果的保留期限只從辨識完成起算，隔天還在校對的結果會在 24 小時整被刪掉；現在從最後一次修改起算。
- 燒錄字幕剛開始的瞬間按取消會回 500。
- 在網頁錄完音還沒送出時點工作清單，錄音會直接被丟掉；現在會提示先送出，或按「關閉」放棄；錄音中或錄完還沒送出時關掉、重新整理分頁，瀏覽器會先詢問（iPhone Safari 不支援這個提示）。
- 刪除、合併或復原後，「分割」可能沿用舊的文字游標位置，切在不對的字；現在只有那段文字沒變時才用游標位置。
- 開到一份已過期的工作，會讓網頁忘掉「上次開的工作」，即使那是另一份還在的工作。
- 連按兩下「錄音」會開出第二個錄音器，第一個的麥克風一直關不掉。
- 遇到「別處改過」（409）後再按復原，舊快照會被放進重新載入後的歷史，再按一次就蓋掉別處的修改。
- 在語者庫刪掉某人後，這份字幕的復原與重做一律失敗；按「記住聲音」後再復原，會悄悄拆掉語者庫的對應。現在復原與重做不改語者庫的對應，`PUT …/transcript` 遇到語者庫已經沒有的 `voice_id` 改存成 `null`，不再回 422。
- 單段文字存失敗時，編輯框被改回修改前的字：超過 500 字會直接被換掉，遇到 409 時畫面和伺服器的內容不一致。現在超過上限先提示、不送出，失敗時顯示伺服器目前的文字。
- 燒錄字幕上傳中重開對話框，進度會消失，還能再燒一次；上傳中切換工作，結果會顯示在另一份工作的對話框。
- 快速連點兩份工作時，晚回來的回應會蓋掉後點的那份。
- 服務沒有提供分辨說話者時，開啟有說話者的舊結果，整份字幕畫不出來。
- 點登入對話框外面會把它關掉。
- Windows 啟動腳本在 `ASR_HOST` 是 `::` 這類 IPv6 位址時會組出錯的網址，瀏覽器不會自動開啟；現在和命令列工具用同一套規則。
- `nvidia-smi` 把記憶體回報成 `[N/A]` 時，自動調校整個失敗。現在只讀顯示卡的編號與名稱，硬體紀錄的 `nvidia_gpus` 不再有 `memory_total_gb`、`memory_free_gb`。

## [0.3.0] - 2026-11-06

### Added

- 必填的 `ASR_ACCESS_TOKEN`（存取權杖）。`ASR_HOST` 不是本機位址時必須設成至少 24 個字元，否則拒絕啟動；只監聽本機時可填 `off`。網頁以 `POST /api/session` 登入（cookie 記住 400 天），API 用 `Authorization: Bearer`，命令列工具與 MCP server 讀同名的環境變數；一分鐘內登入錯 10 次，這一分鐘內的登入一律拒絕。
- 必填的 `ASR_ALLOWED_HOSTS`（接受的主機名稱，逗號分隔）。`Host` 標頭不在清單裡的請求一律回 400，擋下 DNS rebinding；清單必須包含 `ASR_HOST` 對應的本機位址，否則拒絕啟動。
- 從手機或平板使用：`docs/deployment.md` 說明 `tailscale serve`（iPhone Safari 要 HTTPS 才能錄音）與區網自簽憑證的限制。
- `docs/benchmarks.md` 補上 RTX 5060 Ti 的 CUDA 實測與各種優化做法的比較。
- 硬體偵測多記錄 `torch_build`（PyTorch 是 CUDA、ROCm 或 CPU 版）與 OpenVINO 裝置的 `architecture`。

### Changed

- **升級注意**：Docker 部署與 `ASR_HOST` 設成 `0.0.0.0` 的電腦，要先在 `.env` 設定 `ASR_ACCESS_TOKEN` 與 `ASR_ALLOWED_HOSTS` 才能啟動。安裝與啟動腳本會補上 `ASR_ACCESS_TOKEN=off` 與 `ASR_ALLOWED_HOSTS=127.0.0.1,localhost`；從區網 IP、`.ts.net` 網址或反向代理連線的，要把網址裡的名稱加進清單。
- 觸控裝置：按鈕與輸入框約 40 像素高，輸入框的字改成 16 像素（iPhone 不再自動放大頁面），右側面板加寬；在波形上輕點跳到該處、左右拖曳移動播放位置，上下滑動只捲動頁面。
- 以 http 從其他裝置按「錄音」時，提示改用 HTTPS 網址開啟；開始錄音時主動恢復音訊處理，避免音量表不動。

### Fixed

- 手機寬度下需要水平捲動（工具列撐寬頁面、播放到結尾時播放頭超出右緣）、字幕面板標題擠成一字一行，以及窄版頁面的工具列被拉開。
- 自動調校會用 OpenVINO 測 NVIDIA 顯示卡（透過驅動附的 OpenCL），int8 比 CPU 慢 2.6 倍、fp16 無法執行。現在廠商代碼不是 Intel 的 GPU 只用 PyTorch 測。
- 自動調校載入 PyTorch 權重前不檢查剩餘顯示記憶體，空間不夠時會用到 CUDA 記憶體不足。現在先比較預估用量與那張卡當下的剩餘量，不夠就改用其他裝置；有模型開啟句子分段時，預估包含逐字時間戳記多用的約 0.25 GB。
- PyTorch 句子分段會留下每一層的 attention，長錄音還會因為 PyTorch 快取的碎片越佔越多。現在只保留 DTW 要用的 23 個 cross-attention head，推論行程也改用 `expandable_segments`，逐字時間不變；Breeze ASR 26 長錄音的顯示記憶體峰值從 7.7 GB 降到 3.7 GB，速度從 0.068 加快到 0.036。
- 在「硬體」改用 PyTorch 的設定時，網頁行程會在 GPU 上留下 CUDA context；剩餘顯示記憶體改在短暫的子行程裡讀。
- 看不到 GPU 時，「硬體」的提示改為檢查 `CUDA_VISIBLE_DEVICES` 與驅動，不再叫人改裝 CUDA 版 PyTorch（ROCm 版同理）。
- 「硬體」裡的 CPU 名稱多了結尾空白。
- Breeze ASR 26 句子分段的段落起點常比實際開口早約 0.5 秒：停頓後與每個 30 秒視窗的第一個字會把停頓算進自己的時間。現在這種字只保留一個字長中位數，台語例句的起點誤差中位數從 0.53 秒降到 0.20 秒。

## [0.2.7] - 2026-11-05

### Changed

- `python-multipart` 由 0.0.32 升級到 0.0.33。上游修正了沒有檔名的上傳寫入磁碟時出錯，以及兩種解碼的邊界狀況；新增的表單欄位上限預設不開，上傳行為不變。

## [0.2.6] - 2026-11-03

### Added

- `docs/openapi.yaml`：以 OpenAPI 3.2 撰寫的完整 API 規格，涵蓋全部 23 個端點的請求欄位、每個狀態碼的回應格式、錯誤訊息範例，以及工作、燒錄之間的連結關係。`tests/test_openapi.py` 會檢查規格列的端點、成功狀態碼、匯出格式與版本號和程式一致。
- MCP server 依照 MCP `2026-07-28` 規格補齊，仍相容以 `initialize` 握手的舊版用戶端，並回報標題、版本、網址與圖示。
- MCP 新增 `cancel_job`、`rename_speaker` 兩個工具。`transcribe` 可以用 `use_glossary` 套用網頁詞彙表的熱詞；`wait` 為 `true` 時會送出進度通知。
- 每個 MCP 工具都有標題、參數說明與 annotations（`readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint`），用戶端可以據此決定呼叫前要不要先問使用者。除了 `get_transcript`，每個工具也都有 `outputSchema`。
- MCP 資源：詞彙表 `breeze-asr://glossary`，以及每種文字格式的逐字稿 `breeze-asr://jobs/{job_id}/transcript.md` 等。
- MCP 提示：`meeting_minutes` 整理會議紀錄、`proofread_transcript` 校對逐字稿；提示與資源的 `job_id` 可以自動補全。
- `tests/test_mcp_server.py` 測試每個工具、資源、提示與補全，並檢查列舉值與 `docs/openapi.yaml` 一致。

### Changed

- MCP 的 `job_status`、`transcribe` 多回傳檔名、模型、音訊長度、說話者清單與逐字稿的資源 URI。`list_jobs` 改為回傳 `{"jobs": [...]}`，工作編號的欄位名稱和其他工具一樣是 `job_id`。

### Fixed

- `docs/api.md` 熱詞計數範例的 token 數改為實際值 15。
- MCP 工具出錯時（找不到檔案、格式不支援、連不上服務），用戶端只收到「Error executing tool …」，看不到原因，現在會連原因一起回傳。

## [0.2.5] - 2026-11-01

### Fixed

- OpenVINO 與 NNCF 預設會把用量統計（套件版本、作業系統、權重壓縮參數與一組固定的識別碼，不含錄音與字幕）送到 Google Analytics，與 `SECURITY.md` 列出的對外連線不符。推論子行程啟動時改為先寫入 OpenVINO 的設定檔把它關閉，這台電腦上其他 OpenVINO 工具的用量回報也會一起關閉。

## [0.2.4] - 2026-11-01

### Changed

- 分辨說話者第一輪分群的門檻 `cluster_threshold` 由 0.5 改為 0.65：遠距收音的多人會議錯誤率略降，自動判斷的人數也比較穩（實測見 `docs/benchmarks.md`）。自訂過 `models.json` 的話請自行改這一欄。

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

[Unreleased]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.7...v0.3.0
[0.2.7]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Scorpio-meow/Breeze-ASR/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Scorpio-meow/Breeze-ASR/releases/tag/v0.1.0
