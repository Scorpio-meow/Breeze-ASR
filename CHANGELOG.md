# Changelog

格式依照 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號依照 [語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

## [0.7.6] - 2026-11-14

### Fixed

- 跑過自動調校的推論行程，用內顯推論時關不掉：關閉服務要等 10 秒才被強制終止，實測甚至會一直卡在結束階段、佔滿一個 CPU 核心。原因是自動調校在推論行程裡另外建立了一個 OpenVINO Core 查詢內顯的屬性，之後同一個行程再把模型載到內顯，結束時就卡住。現在改在獨立的子行程偵測硬體。

## [0.7.5] - 2026-11-14

### Changed

- 日誌把做為壓縮基礎的那次匯出寫成「以原始精度（不壓縮）匯出成 OpenVINO」，不再寫成「轉成 OpenVINO fp32」，免得以為自動調校略過 fp32 後又在測 fp32。

## [0.7.4] - 2026-11-14

### Security

- 相依套件 `opentelemetry-api` 升到 1.45.2，修補解析 `traceparent` 時可能耗盡 CPU 的問題（ReDoS）。ListenMiao 只有 MCP server 會從本機 AI 工具送來的請求讀這個值，網頁服務不受影響。

## [0.7.3] - 2026-11-13

### Changed

- 命令列工具只剩一份：`python -m app.cli` 直接執行 Skill 附的 `skills/listenmiao/scripts/listenmiao.py`，MCP server 與 Windows 啟動腳本也用這支腳本連服務，原本另一份 HTTP 用戶端 `app/client.py` 刪除，不會再改得不一致。Docker 映像也放進這支腳本。
- 新的工作 id 與語者 id 改用十六進位字串。原本約每 64 個 id 就有 1 個以 `-` 開頭，命令列工具會把它當成選項而失敗；之前產生、以 `-` 開頭的 id，要放在所有選項之後並在前面加上 `--`。
- 發布流程：推 tag 時先跑完 CI 才發布 GitHub Release；Docker 映像只在推 `v*` tag 時建置，而且 tag 要和 `pyproject.toml` 的版本一致；`astral-sh/setup-uv` 與 `jlumbroso/free-disk-space` 改用 commit SHA 固定版本。
- 發布的 zip 裡 `.sh` 不再標成可執行，和 git 裡一致；文件與腳本的提示一律寫 `bash scripts/…`。

### Fixed

- 命令列工具與 MCP server 不再經過系統或環境變數設定的 proxy，也不跟著轉址走。Windows 設了公司 proxy 時，連 `127.0.0.1` 的請求（含存取權杖與錄音）原本會先送到 proxy，常見結果是只看到「HTTP 502」；遇到轉址時，權杖也不會再被帶到別的主機。
- 工作還在等分辨說話者的模型（第一次下載，或離線時重試）時按取消，原本辨識照樣開始並跑完整段，現在直接結束。
- 自動標點失敗時，命令列工具與 MCP 的完成訊息會附上原因，例如「完成，25 段，自動標點失敗：…」。
- 批次辨識：兩個檔案的結果會寫到同一個位置時，一開始就停下，不再互相覆蓋；某個結果寫不出去（例如檔案正在 Word 裡開著）時，印出原因與工作 id，其他檔案照常寫出。
- 命令列工具讀回應讀到一半斷線（例如服務被關掉或正在更新）時，改成說明連不上服務，不再印出 traceback，批次辨識的其他檔案也照常寫出。
- 命令列工具與 MCP 的 `--url`／`ASR_HOST`、`ASR_PORT` 指到別的網頁服務時，改成說明回應不是 JSON、請確認網址，不再印出 JSON 解析錯誤。
- MCP 送出錄音後，等服務收檔與解碼不再有 60 秒上限，長錄音不會因為逾時被當成失敗，重試時也不會多出重複的工作。
- 更新時換掉的檔案保留原本的權限，自己加上執行權限的 `.sh` 更新後仍然可以直接執行。
- `scripts/install.sh`：`~/.local/bin` 已經在 PATH 裡時，裝完 uv 不再中止。
- `scripts/install.ps1`：Agent Skill 裝不起來時停下並說明原因，不再接著印「安裝完成」。CI 也會檢查 `scripts/update.sh` 的語法。
- 網頁：還沒送出的錄音，不會再被選檔、拖檔、再按一次錄音或「對應原始檔」直接換掉，會先提醒送出或關閉；之後再選同一個檔案也照常有反應。
- 網頁：Chrome、Edge 錄的 WebM 讀不到長度，超過 30 分鐘的錄音原本會在瀏覽器裡整段解碼成波形，可能把分頁撐爆；現在改用錄音秒數判斷。
- 網頁：錄音分頁切到背景時，錄音長度上限照樣生效，不會錄超過上限、送出時整段被拒絕。
- 網頁：燒錄字幕時網路斷一下不再直接顯示失敗，會繼續查進度；切到別的工作再打開燒錄視窗，也不會丟掉進行中的燒錄。
- 網頁：說話者改名與「記住聲音」也排進儲存佇列，改完名字馬上合併、分割或復原不再收到版本衝突；伺服器版本跳號時（例如別人同時在改），清掉復原紀錄，不會用舊的快照蓋掉別人的修改。
- 網頁：在對話框裡拖曳選字、放開時滑鼠在對話框外，不再關掉對話框、丟掉沒存的內容。
- 網頁：「重新調校」按鈕改用共用的兩段確認按鈕，讀螢幕軟體會知道它進入待確認狀態。

## [0.7.2] - 2026-11-13

### Added

- 命令列工具與 Skill 附的 `listenmiao.py` 的 `status` 多印一行服務的版本與發布日期；0.6.0 以前的服務沒有回報版本，會照實說明。

### Changed

- Agent Skill 補上 0.6.0 起的安裝與更新方式：從 Releases 下載 zip 安裝、安裝腳本詢問是否裝上 Skill、在網頁上更新、`scripts/update.ps1`／`update.sh`、0.6.0 以前的版本怎麼更新、退回更新前的版本，以及 0.7.0 以前的 Docker 版改名後要加的設定；API 參考補上 `GET`／`POST /api/update` 與 `/api/config` 的 `version`。
- Agent Skill 不再把安裝資料夾稱為 git checkout，用 zip 安裝的也會選用已安裝的命令列工具；MCP 工具清單補上 `list_models`。
- `evals/` 裡兩個評分檔的檔名換成新名字。

## [0.7.1] - 2026-11-13

### Fixed

- 網頁更新時提示移除舊的 `breeze-asr` Skill，指令改成 `bunx skills remove breeze-asr -g`；少了 `-g` 只會找目前資料夾，刪不到照 README 用 `-g` 裝在全域的那份。`docs/cli.md` 裡 `bunx skills` 的更新與移除指令也一起加上 `-g`。

## [0.7.0] - 2026-11-13

### Changed

- 專案改名為 ListenMiao（聽喵）：GitHub repo 改成 `Scorpio-meow/ListenMiao`，官網改到 <https://scorpio-meow.github.io/ListenMiao/>，舊的 repo 網址會自動轉過去。網頁、文件、命令列、MCP server 的顯示名稱、新安裝時的 Windows 桌面捷徑與 LOGO 字樣都換成新名字；「Breeze ASR」之後只指 MediaTek Research 的模型。
- 識別名稱也改成新名字：
  - Agent Skill 從 `breeze-asr` 改名為 `listenmiao`（`/listenmiao`、`skills/listenmiao/scripts/listenmiao.py`）。安裝腳本裝的會在網頁更新時換成新名字：從 0.6.x 更新到 0.7.0 時先維持舊名 `breeze-asr`（內容換成新版，照樣用 `/breeze-asr`），下一次更新再換成 `listenmiao`；用 `bunx skills` 裝過舊名的，先 `bunx skills remove breeze-asr -g` 再重新安裝。
  - 發布檔改名為 `listenmiao-<版本>.zip` 與 `listenmiao-manifest.json`；每次發布仍另外附上舊名的 `breeze-asr-<版本>.zip`，讓 0.6.x 能直接在網頁上更新。
  - MCP server 名稱改為 `listenmiao`，資源網址改為 `listenmiao://…`。
  - Docker Compose 的專案與服務名稱改為 `listenmiao`，容器固定叫 `listenmiao`，資料放在 `listenmiao_asr-data` volume。已經在用 Docker 版的，先在 `.env` 加上 `COMPOSE_PROJECT_NAME=breeze-asr`，繼續用原本的 `breeze-asr_asr-data`，再用 `docker compose up -d --remove-orphans` 換掉舊容器；沒加的話會因網段和舊的網路重疊而無法啟動，或在舊專案已經關掉時改用一個空的新 volume（舊資料仍在 `breeze-asr_asr-data`，加上這行就會回來）。
  - 發布的映像檔改到 `ghcr.io/scorpio-meow/listenmiao`，舊的 `ghcr.io/scorpio-meow/breeze-asr` 不再更新，`.env` 的 `ASR_IMAGE` 要改成新名稱。
  - 日誌檔改為 `listenmiao.log` 與 `listenmiao-crash.log`，舊日誌留在原處；套件名改為 `listenmiao`。
  - 登入 cookie、瀏覽器記住的選項與模型轉換紀錄沿用舊名，不必重新登入、重設或重新轉換模型；舊的桌面捷徑用的 `scripts/breeze-asr.ico` 也留著，圖示不會消失。

## [0.6.1] - 2026-11-12

### Changed

- `pyproject.toml` 補上專案說明、關鍵字與分類，以及官網、文件、原始碼、Issues、CHANGELOG、Releases 的網址；`Homepage` 維持指向 GitHub repo，更新程式靠它找到下載位置。

## [0.6.0] - 2026-11-12

### Added

- 不用 git 的檢查更新與自動更新：網頁右下角顯示版本號，按下後可以「檢查更新」（這時才連到 GitHub）與「立即更新」。服務關閉後由啟動腳本下載新版 zip、核對 SHA-256、備份並換上新版的檔案、重裝套件，再重新啟動；`.env`、資料目錄與改過的檔案都保留，改過的檔案的新版另存成 `.new`，套件裝不起來時還原。版本發布超過 90 天時，版本號旁顯示發布了幾天。
- `scripts/update.ps1`、`scripts/update.sh`：服務沒開時也能更新；0.6.0 以前的版本下載這支腳本後就能更新，靠檔案指紋認出裝的是哪一版、哪些檔案改過。
- 推 tag 時建立 GitHub Release，附上只含執行所需檔案的 zip、SHA-256、檔案清單與更新程式；手動觸發時替還沒有 Release 的舊 tag 補建。
- 安裝腳本最後問你要不要裝上 Agent Skill（不需要 git，直接按 Enter 就是安裝），放到 `~/.claude/skills` 與 `~/.agents/skills`（Codex、Cline 等工具共用）；更新 Breeze ASR 時，用安裝腳本裝的 Skill 一起換成新版；用 `bunx` 或 `npx` 裝的，照樣用 `bunx skills update`（或 `npx`）更新。
- `GET /api/update`、`POST /api/update`；`GET /api/config` 多了 `version`。
- `python -m app` 多了 `--updatable`，由啟動腳本加上。

## [0.5.0] - 2026-11-12

### Added

- macOS（Apple Silicon、macOS 14 以上）可以用 `scripts/install.sh`、`scripts/start.sh` 安裝與啟動，以 CPU（OpenVINO）推論。Intel Mac 與 macOS 13 以下由安裝腳本直接擋下並說明原因，Intel Mac 改用 Docker。CI 的測試與腳本檢查加上 macOS。

### Changed

- 自動調校：某裝置的 int4 或 int8 失敗時只略過其他壓縮格式，fp16、fp32 照測；浮點格式也失敗才略過整個裝置。
- 內附的 ffmpeg 升到 9.0.2（imageio-ffmpeg 0.8.1）。
- `scripts/install.sh` 建立 `.env` 時提醒先把字幕字型改成這台電腦有的中文字型。

### Fixed

- macOS 沒有 CPU 親和性，自動調校偵測硬體時出錯、本機模型全部不能用；改以邏輯核心數當作可用執行緒數。

## [0.4.0] - 2026-11-08

### Added

- Agent Skill `skills/breeze-asr/`：讓 Claude Code 等 AI 工具操作 Breeze ASR，包括選模型與說話者人數、先問人名與術語再帶熱詞、替說話者改名、整理會議紀錄、燒錄字幕、詞彙表與語者庫，以及服務沒開或出錯時的排除方式。用 `bunx skills add Scorpio-meow/Breeze-ASR --skill breeze-asr -g`（或 `npx`）安裝，Codex、GitHub Copilot、Cursor、Gemini CLI 等支援 Agent Skills 的工具也能用；各工具的資料夾、手動安裝與更新方式見 `docs/cli.md` 的「安裝 Skill」。
- Skill 附的 `scripts/breeze_asr.py`：只用 Python 標準函式庫，指令與參數和 `python -m app.cli` 相同，給只用 Docker 或服務在另一台電腦、沒有專案資料夾的情況。
- 命令列工具新增 `status`（服務、模型狀態與實測速度、上限）、`cancel`、`rename`（替說話者改名）、`render`（燒錄字幕並下載 MP4）、`glossary`（顯示或以 JSON 檔取代詞彙表）與 `voices list`／`remember`／`forget`（語者庫）。中文名字以 UTF-8 送出，Windows PowerShell 不會變成亂碼。

### Changed

- 命令列工具與 MCP `job_status` 的完成訊息加上說話者人數，例如「完成，25 段，說話者 2 位」；分辨說話者失敗時改附原因。
- CI 與提交前檢查的 `ruff` 也檢查 `skills/`。

### Fixed

- Windows 上 `POST /api/jobs` 偶爾回 500，工作其實已經排入佇列並照常跑完：命令列工具、MCP 與 Skill 以為送出失敗，`data/jobs/` 還留下一個空的 `tmp…` 資料夾。監看資料夾也會遇到，而且下一輪會把同一個檔案再送一次。原因是每次送出都在 `data/jobs/` 建一個暫存資料夾，工作排入佇列後才刪；有程式監看專案資料夾（例如編輯器）時，會在資料夾剛建好時短暫打開它，Windows 在這段時間刪不掉資料夾，這個錯誤就蓋過了已經成功的送出。現在上傳與解碼的暫存檔直接放在 `data/jobs/`，不另建資料夾；工作排入佇列後暫存檔就算刪不掉也照樣回 202，只在日誌記一筆警告，下次啟動服務時清掉。

## [0.3.4] - 2026-11-08

### Added

- 新的專案 LOGO：吉祥物黑貓從字幕框後探出頭，蠍尾的紅色螫針像一個逗號。`docs/brand/` 有圖標與橫式字標的亮色、暗色、單色版，README 開頭也換上了。
- 網頁 App 圖示：`apple-touch-icon.png`（加到 iPad、iPhone 主畫面時用）與 `manifest.webmanifest`（附 192、512 與 maskable 圖示），瀏覽器可以把服務安裝成 App。
- 網頁工具列的「Breeze ASR」前面加上 LOGO（`static/mark.svg`）。

### Changed

- `static/icon.svg` 換成新 LOGO，網頁分頁與 MCP server 的圖示一起更新；暗色分頁會自動加上淺色外框。
- `/favicon.ico` 改成直接回傳 16、32、48 px 的 ICO 檔，不再轉址到 `icon.svg`，不支援 SVG 圖示的瀏覽器也看得到。
- Windows 安裝腳本建立的桌面捷徑改用新圖示 `scripts/breeze-asr.ico`，不再顯示 PowerShell 的圖示；已經建好的捷徑重新執行 `scripts\install.ps1` 就會更新。

## [0.3.3] - 2026-11-08

### Changed

- `docs/benchmarks.md` 的 Core i7-9700 換成 0.3.2 的實測：CPU・int8 速度 0.66，記憶體不足時兩種 fp16 仍被實測，整輪調校約 35 分鐘。

### Fixed

- 自動調校低估 fp16 的記憶體：以 FP32 運算的 CPU 跑 fp16，實際約 4.8 GB，卻只估約 3.6 GB；量測時正在換頁的結果還會把權重以外的開銷算成 0，連帶低估之後的內顯・fp16。8 GB、只剩 1.8 GB 可用的電腦因此兩種 fp16 都在換頁中實測，整輪調校從約 7 分鐘拉長到約 35 分鐘。現在浮點權重依權重格式與裝置運算精度中較寬的一方估（CPU 的 fp16 約 6.5 GB），量到的記憶體比權重還小時不拿來預估其他格式。

## [0.3.2] - 2026-11-08

### Added

- `docs/benchmarks.md` 新增 Core i5-12500H 筆電（Iris Xe 內顯、40 GB）的實測：自動調校選內顯・int8，速度 0.34，CPU・int8 是 0.42。
- `PRIVACY.md` 隱私權政策：資料存在哪裡、保留多久、瀏覽器存了什麼、什麼情況會連到外面、怎麼關閉用量回報與刪除資料。`SECURITY.md` 原本的資料一節移到這裡。

### Fixed

- Breeze ASR 26 加熱詞一律回 500：網頁的熱詞計數與送出辨識、命令列工具、MCP 的 `transcribe` 都會遇到，監看資料夾設成 26、詞彙表又有熱詞時，每次檢查都失敗。原因是它的 Hugging Face repo 沒有 `tokenizer.json`；現在改用兩個模型都有的 `vocab.json` 與 `merges.txt` 計算 token 數，結果和原本相同。
- 服務自己產生的 `/api/openapi.json` 版本寫成 `0.1.0`，現在和專案版本相同。
- MCP 的 `job_id` 只接受工作編號用的字元（英文字母、數字、`_`、`-`），傳入像路徑的值時直接說明是哪個參數不合格，不會只回「Error executing tool」。
- 命令列工具與 MCP server 找不到服務網址時，訊息改成說明要設定 `ASR_HOST`、`ASR_PORT`；原本叫 MCP 的使用者改用它沒有的 `--url`。
- API 文件：`POST /api/session` 的 411、413 沒寫；`docs/api.md` 另外漏了燒錄上傳的 411、瀏覽器錯誤回報的 429，以及下載影片的 `Range`（206、416）。

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

## [0.1.3] - 2026-10-29

這個 tag 的 `pyproject.toml` 仍標 0.1.0，程式回報的版本是 0.1.0。

### Removed

- Intel NPU 不再納入自動調校：實測時驅動回報 `DEVICE_LOST`、裝置被重置。調校結果把 NPU 列為略過並寫明原因。

## [0.1.2] - 2026-10-29

這個 tag 的 `pyproject.toml` 仍標 0.1.0，程式回報的版本是 0.1.0。

### Changed

- 編譯快取依轉換後的模型分資料夾存放；推論子行程每次啟動都會刪掉對應模型已不存在的快取，快取不再無限增長。

## [0.1.1] - 2026-10-29

這個 tag 的 `pyproject.toml` 仍標 0.1.0，程式回報的版本是 0.1.0。

### Fixed

- 自動調校預估 fp16、fp32 需要的記憶體時，權重大小改用權重格式本身的位元數，不再以裝置的運算精度計算。原本 CPU 上的 fp16 以每個參數 4 bytes 估算，可能誤判放不下而略過不測。

## [0.1.0] - 2026-10-29

第一個版本。

### Added

- 網頁 APP：上傳音訊或影片、拖進視窗或瀏覽器錄音，工作排隊並在狀態列顯示排隊位置、階段與預估時間。
- 字幕編輯軟體式的介面：工具列、波形與時間尺、說話者軌道、字幕列表、右側面板與狀態列；時間軸可點擊或拖曳定位，空白鍵播放／暫停，播放時字幕列表跟著捲動。
- 支援 Breeze ASR 25 與 Breeze ASR 26，模型清單由 `models.json` 指定並固定版本。
- 三種推論方式：本機 OpenVINO、本機 PyTorch、轉呼叫既有的 Breeze ASR 服務。
- 自動調校：第一次啟動時依 OpenVINO 裝置回報的運算精度，實測各裝置與權重格式（int4、int8、fp16、fp32），選出最快且輸出正確的推論設定；只轉換要實測的格式，放不下的格式在轉換前就略過，NPU 排在最後測、失敗只淘汰 NPU。可重新調校、在「硬體」改用其他實測過的設定，或手寫硬體設定檔。
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
- 超過 30 分鐘或瀏覽器無法解碼的音訊，送出辨識後由伺服器產生波形。
- 在不能錄音的網址按「錄音」，狀態列說明原因與瀏覽器的設定方式。
- 所有回應加上 `Cache-Control: no-cache`，更新版本後瀏覽器不會繼續使用舊的前端程式。

[Unreleased]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.7.6...HEAD
[0.7.6]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.7.5...v0.7.6
[0.7.5]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.7.4...v0.7.5
[0.7.4]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.7.3...v0.7.4
[0.7.3]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.7.2...v0.7.3
[0.7.2]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.7...v0.3.0
[0.2.7]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Scorpio-meow/ListenMiao/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Scorpio-meow/ListenMiao/releases/tag/v0.1.0
