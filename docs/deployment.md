# 部署

[README](../README.md) · **部署** · [設定](configuration.md) · [運作方式](how-it-works.md) · [API](api.md) · [命令列與 MCP](cli.md) · [效能實測](benchmarks.md)

安裝、啟動、更新與日常維運。每個環境變數的說明見[設定](configuration.md)。

**本頁**：[Windows](#windows-安裝腳本) · [Linux](#linux-安裝腳本) · [macOS](#macos-安裝腳本) · [Docker](#docker) · [第一次啟動](#第一次啟動) · [更新版本](#更新版本) · [讓其他電腦連線](#讓其他電腦連線) · [從手機或平板使用](#從手機或平板使用) · [監看資料夾](#監看資料夾) · [日誌](#日誌) · [字幕字型](#字幕字型) · [資源限制與轉呼叫](#資源限制與轉呼叫) · [限制](#限制) · [GPU 支援](#gpu-支援) · [不支援或未實測的硬體](#不支援或未實測的硬體)

| 方式 | 適用 | GPU |
| --- | --- | --- |
| [Windows 安裝腳本](#windows-安裝腳本) | 個人電腦 | Intel 內顯（OpenVINO）、NVIDIA（CUDA），自動偵測 |
| [Linux 安裝腳本](#linux-安裝腳本) | 個人電腦、伺服器 | Intel 內顯（OpenVINO）、NVIDIA（CUDA）、AMD（ROCm），自動偵測 |
| [macOS 安裝腳本](#macos-安裝腳本) | Apple Silicon 的 Mac | 不用 GPU，以 CPU（OpenVINO）推論 |
| [Docker](#docker) | 伺服器 | 沒有顯示卡用 `compose.yaml`；NVIDIA 再疊加 `compose.cuda.yaml`，AMD（只限 Linux 主機）再疊加 `compose.rocm.yaml`。不支援 Intel 內顯，Docker Desktop 上只能用 CPU |

## Windows 安裝腳本

從 [Releases](https://github.com/Scorpio-meow/ListenMiao/releases/latest) 下載 `listenmiao-<版本>.zip` 解壓縮（不需要 git；也可以 clone 本專案）後，在專案根目錄的 PowerShell 執行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

腳本會安裝 [uv](https://docs.astral.sh/uv/)（還沒裝的話）、建立 Python 環境、從 `.env.example` 建立 `.env`，並在桌面建立「ListenMiao」捷徑。偵測到 `nvidia-smi` 時裝 CUDA 版 PyTorch，否則裝 CPU 版。

| 參數 | 作用 |
| --- | --- |
| `-Device cpu`、`-Device cuda` | 指定 PyTorch 版本 |
| `-NoShortcut` | 不建立桌面捷徑 |

之後點桌面捷徑或執行 `scripts\start.ps1` 啟動，服務就緒後自動開啟瀏覽器（加 `-NoBrowser` 則不開），關閉視窗即停止服務。啟動失敗或服務異常結束時，視窗會停住並顯示原因，按 Enter 才關閉。

不用腳本也可以手動執行，再開啟 <http://127.0.0.1:7860>：

```powershell
Copy-Item .env.example .env
uv sync --frozen
uv run --env-file .env python -m app
```

模型權重的下載快取沿用 Hugging Face 的 `HF_HOME`（通常是 `%USERPROFILE%\.cache\huggingface`）。

## Linux 安裝腳本

在專案根目錄：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

偵測到 `nvidia-smi` 時裝 CUDA 版 PyTorch、偵測到 `rocm-smi` 或 `amd-smi` 時裝 ROCm 版，否則裝 CPU 版；可以用 `bash scripts/install.sh cpu`、`cuda` 或 `rocm` 指定。ROCm 需要主機已安裝 AMD 的 ROCm 驅動，且使用者在 `video`、`render` 群組。

## macOS 安裝腳本

需要 Apple Silicon（M 系列晶片）與 macOS 14 以上。在專案根目錄的終端機：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

- 推論只用 CPU（OpenVINO）；Apple 的 GPU 與神經網路引擎都用不到。
- 第一次啟動前，把 `.env` 的字幕字型改成這台 Mac 有的中文字型，見[字幕字型](#字幕字型)；`.env.example` 的微軟正黑體在 Mac 上找不到，服務會拒絕啟動。
- Intel Mac 無法原生安裝：OpenVINO 2026 與 PyTorch 2.3 起都沒有 Intel Mac 版，安裝腳本會直接停下。改用 [Docker](#docker) 的 CPU 版。
- Apple Silicon 上用 Docker 時要在本機建置（`docker compose up -d --build`）：發布的映像檔只有 x86（`linux/amd64`），在 Apple Silicon 上要靠模擬執行，會明顯變慢。

## Docker

### CPU

Docker Desktop（WSL2）的容器拿不到 Intel 內顯，所以 Docker 版在這種環境只用 CPU，適合沒有 Intel 內顯的 Linux 伺服器。容器內一律監聽 `0.0.0.0`，複製 `.env` 後要先把 `ASR_ACCESS_TOKEN` 設成一組權杖（見[設定](configuration.md#存取權杖)），否則服務會拒絕啟動：

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

- 照 `.env.example` 的 `ASR_ALLOWED_HOSTS` 就能在這台主機用 `http://127.0.0.1:7860` 開啟；從其他電腦連線見[讓其他電腦連線](#讓其他電腦連線)。
- 資料（權重快取、轉換後的模型、硬體設定檔、辨識結果、詞彙表、語者庫、日誌）存在 `listenmiao_asr-data` volume，重建映像檔不會重新下載或重新調校。
- 確認 `ASR_DOCKER_SUBNET` 的網段不會和現有的網路衝突。
- 轉換 Breeze ASR 26（原始權重 6 GB）時容器記憶體峰值約 6 GB，Docker Desktop 建議至少配置 8 GB（WSL2 預設為實體記憶體的一半）。

### NVIDIA GPU（CUDA）

需要 NVIDIA Container Toolkit。在 `.env` 設定：

```dotenv
COMPOSE_FILE=compose.yaml:compose.cuda.yaml
COMPOSE_PATH_SEPARATOR=:
ASR_TORCH_GROUP=cu130
ASR_IMAGE=listenmiao:cuda
ASR_CUDA_DEVICE_ID=0
```

- `ASR_CUDA_DEVICE_ID` 是主機上 `nvidia-smi` 的 GPU 編號（依 PCI 匯流排排序）。容器裡只看得到這一張卡，`nvidia-smi` 與 PyTorch 都把它編成 0，自動調校會同時實測它（`cuda:0`）與 CPU。
- 容器裡的 OpenVINO 也看得到這張卡，但自動調校只讓 PyTorch 用 NVIDIA 卡，原因見[自動調校](how-it-works.md#自動調校)。
- 映像檔約 10 GB。

### AMD GPU（ROCm）

只支援 Linux 主機，需要已安裝 AMD 的 ROCm 驅動。在 `.env` 設定：

```dotenv
COMPOSE_FILE=compose.yaml:compose.rocm.yaml
COMPOSE_PATH_SEPARATOR=:
ASR_TORCH_GROUP=rocm72
ASR_IMAGE=listenmiao:rocm
ASR_VIDEO_GID=44
ASR_RENDER_GID=110
```

- `ASR_VIDEO_GID`、`ASR_RENDER_GID` 填主機上 `getent group video render` 顯示的 GID，容器靠這兩個群組存取 `/dev/kfd` 與 `/dev/dri`。
- 不在 ROCm 官方支援清單的消費級顯示卡，可能需要在 `.env` 加上 `HSA_OVERRIDE_GFX_VERSION`。
- 映像檔很大（10 GB 以上）。

### 使用發布的映像檔

推送 `v*` 標籤時，GitHub Actions 會建置三種映像檔，發布到 GitHub Container Registry；每個版本另有 `<版本>-cpu` 這類固定標籤。

| 標籤 | PyTorch |
| --- | --- |
| `ghcr.io/<owner>/<repo>:cpu` | CPU |
| `ghcr.io/<owner>/<repo>:cuda` | CUDA 13.0 |
| `ghcr.io/<owner>/<repo>:rocm` | ROCm 7.2 |

在 `.env` 把 `ASR_IMAGE` 設成對應的映像檔（GPU 版也照上面設定 `COMPOSE_FILE` 等），然後：

```bash
docker compose pull
docker compose up -d --no-build
```

## 第一次啟動

不論用哪種方式部署，第一次啟動都會：

- 下載兩個模型的原始權重，需要連上 Hugging Face；分辨說話者與自動標點的模型（共約 330 MB）在背景下載。
- [自動調校](how-it-works.md#自動調校)：實測各裝置與權重格式，挑出最快的設定。在 Core i7-9700（8 GB 記憶體）上整輪約 7–9 分鐘，其他機器見[效能實測](benchmarks.md#辨識速度與自動調校)。期間暫時多佔約 4–7 GB 磁碟，調校完只留選中的格式。
- 把另一個模型轉成選中的格式；Breeze ASR 26 轉 int8 在 Core i7-9700 上約 7 分鐘。

進度顯示在狀態列；預設模型準備好就能先用，之後啟動約 15 秒就緒。

- 記憶體：轉換期間峰值約 9.5 GB；8 GB 記憶體的電腦也能完成，但會大量使用分頁檔、比較慢。
- 磁碟：Hugging Face 原始權重約 8.7 GB（只在轉換時需要）、轉換後的 int8 模型約 3 GB、內顯編譯快取約 3.7 GB。

## 更新版本

從 [Releases](https://github.com/Scorpio-meow/ListenMiao/releases/latest) 下載 zip 安裝的，不需要 git 就能更新。

**在網頁上更新**：用桌面捷徑或 `scripts/start` 啟動時，按右下角的版本號，再按「檢查更新」。有新版會出現「立即更新」，按下後：

1. 服務關閉，啟動腳本從 GitHub 下載新版的 zip，核對 SHA-256 檢查碼；不符就停下，不動任何檔案。
2. 把要換掉或刪掉的檔案備份到 `.update/backup/`，換上新版，再依你原本的顯示卡重裝套件（`uv sync`）。
3. 重新啟動服務，網頁自動重新載入。套件裝不起來時還原成更新前的檔案，用原本的版本啟動，原因顯示在啟動視窗。

`.env`、資料目錄（辨識結果、語者庫、詞彙表、日誌）、模型與 `.venv` 都不會動。你改過的檔案（例如 `models.json`）保留你的版本，新版另存成同名的 `.new` 檔，啟動視窗會列出來。版本發布超過 90 天時，版本號旁會顯示發布了幾天，提醒你檢查。

**服務沒開或起不來時**：關閉服務後執行更新腳本，會先問要不要更新：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\update.ps1
```

```bash
bash scripts/update.sh
```

**0.6.0 以前的版本**沒有更新腳本，先在專案資料夾下載一次，再照上面執行（之後就能在網頁上更新）：

```powershell
Invoke-WebRequest https://github.com/Scorpio-meow/ListenMiao/releases/latest/download/update.ps1 -OutFile scripts\update.ps1
```

```bash
curl -fsSL -o scripts/update.sh https://github.com/Scorpio-meow/ListenMiao/releases/latest/download/update.sh
```

更新程式會比對檔案指紋，認出你裝的是哪一版、哪些檔案改過。0.3.0 以前的版本沒有記錄下載位置，要到 Releases 手動下載新版，再把 `.env` 與資料目錄搬過去。

要退回更新前的檔案：關閉服務後執行 `uv run --no-sync python -m app.update --root . rollback`，再執行 `uv run --no-sync python -m app.update --root . group` 印出套件組別，用 `uv sync --frozen --no-default-groups --group <組別>` 重裝套件。

**用 git 取得的**：

```powershell
git pull
uv sync --frozen
```

NVIDIA 顯示卡改用 `uv sync --frozen --no-default-groups --group cu130`（AMD 換成 `rocm72`），或重跑安裝腳本。

**Docker**：`git pull` 後 `docker compose up -d --build`；用發布的映像檔則是 `docker compose pull`、`docker compose up -d --no-build`。

從 0.6.x 以前的 Docker 版更新上來時，先在 `.env` 加上 `COMPOSE_PROJECT_NAME=breeze-asr`，繼續用原本的 `breeze-asr_asr-data` volume，第一次啟動時再加上 `--remove-orphans` 換掉舊容器。用發布的映像檔的，`ASR_IMAGE` 也要改成 `ghcr.io/scorpio-meow/listenmiao:<標籤>`。

> [!IMPORTANT]
> 新版本可能新增 `.env` 變數，見 [CHANGELOG](../CHANGELOG.md)。安裝腳本只在 `.env` 不存在時才建立它；已有 `.env` 時，安裝與啟動腳本會把還沒有的變數依 `.env.example` 補在檔尾並列出來，已經設定的值不會被改。Docker 不經過啟動腳本，缺少新的變數時服務會拒絕啟動並說出缺少哪一個；照 `.env.example` 手動補上，或在主機上執行 `uv run --no-sync python -m app.envfile .env.example .env`。

## 讓其他電腦連線

> [!WARNING]
> 連得到服務、又有存取權杖的人，都能讀取、修改所有辨識結果與語者庫。開放前請先看 [SECURITY.md](../SECURITY.md)。

1. 產生一組存取權杖，填入 `.env` 的 `ASR_ACCESS_TOKEN`（見[設定](configuration.md#存取權杖)）。`ASR_HOST` 不是本機位址時，沒有權杖服務會拒絕啟動。
2. 原生執行：把 `.env` 的 `ASR_HOST` 改成 `0.0.0.0`，並確認防火牆允許該埠。Docker：把 `ASR_PUBLISH_ADDRESS` 改成 `0.0.0.0`。
3. 把其他電腦要用的 IP 或名稱加進 `ASR_ALLOWED_HOSTS`，例如 `ASR_ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.10`（見[設定](configuration.md#接受的主機名稱)）；沒列入的名稱會回 400。
4. 其他電腦開啟 `http://<這台電腦的 IP>:7860`，輸入權杖；同一個瀏覽器之後不必再輸入。

以 http 連線時，權杖、錄音與逐字稿都以明文傳送，只適合信任的區網。瀏覽器也只允許 HTTPS 或 `localhost` 的網頁使用麥克風，以 http 開啟時按「錄音」只會提示原因。要錄音或網路不完全可信時，改用[下一節](#從手機或平板使用)的 HTTPS 做法。

只想在區網某台桌機的 Chrome 或 Edge 測試錄音，可以在那台電腦開 `chrome://flags/#unsafely-treat-insecure-origin-as-secure`（Edge 是 `edge://flags/#unsafely-treat-insecure-origin-as-secure`），把「Insecure origins treated as secure」改成 Enabled，填入服務的完整網址（含 `http://` 與埠號，例如 `http://192.168.1.10:7860`，多個用逗號分隔），再按 Relaunch。這個設定只對那台電腦的瀏覽器有效，手機瀏覽器沒有這個選項。

## 從手機或平板使用

辨識照樣在這台電腦上跑，手機與平板只用瀏覽器開網頁：上傳或錄音、播放、校對、匯出。iPhone、iPad 的 Safari 只在 HTTPS 網頁開放麥克風，所以要用 HTTPS 連線。

下面兩種做法，ListenMiao 都維持只監聽 `127.0.0.1:7860`，轉來的請求在服務看來都來自本機，服務不會強制要求權杖。請一定設定 `ASR_ACCESS_TOKEN`：網路裡有別人的裝置、或手機遺失時，權杖是最後一道防線。手機網址裡的名稱或 IP 也要以逗號接在 `ASR_ALLOWED_HOSTS` 的 `127.0.0.1,localhost` 後面，沒列入會回 400（見[設定](configuration.md#接受的主機名稱)）。

### 用 tailscale serve（建議）

[Tailscale](https://tailscale.com/) 讓登入同一個 tailnet 的裝置互相連線。`tailscale serve` 在這台電腦提供 HTTPS 網址，憑證由 Tailscale 自動申請與更新，只有 tailnet 裡的裝置連得到，網際網路上的其他人連不到，也不必開防火牆。

1. 這台電腦與手機都安裝 Tailscale，登入同一個 tailnet。
2. tailnet 要開啟 MagicDNS 與 HTTPS Certificates（Tailscale 管理頁面的 DNS 分頁）；第一次執行 `tailscale serve` 時如果還沒開，會印出開啟的網址。
3. 在 `.env` 設定 `ASR_ACCESS_TOKEN`。
4. 啟動 ListenMiao 後，在這台電腦執行：

   ```powershell
   tailscale serve --bg 7860
   ```

   會印出 `https://<電腦名稱>.<tailnet 名稱>.ts.net/` 這類網址。`--bg` 讓這個設定在背景持續生效，直到關閉為止。
5. 把網址裡的 `<電腦名稱>.<tailnet 名稱>.ts.net` 加進 `ASR_ALLOWED_HOSTS`，重新啟動 ListenMiao。`tailscale serve` 轉送時保留手機網址裡的名稱。
6. 手機開啟 Tailscale app 並連線，用 Safari 或 Chrome 開啟該網址，輸入權杖；第一次按「錄音」時允許使用麥克風。

| 指令 | 作用 |
| --- | --- |
| `tailscale serve status` | 列出目前開放的網址 |
| `tailscale serve reset` | 停止開放（會清掉這台電腦所有的 `tailscale serve` 設定） |

- 不要用 `tailscale funnel`，它會把服務開放到整個網際網路。
- HTTPS 憑證由公開的憑證機構簽發，電腦名稱與 tailnet 名稱會出現在公開的憑證透明度紀錄；不想公開的名稱請先在 Tailscale 改名。

### 區網自簽憑證

沒有 Tailscale 時，也可以在這台電腦跑 HTTPS 反向代理（例如 Caddy 的 `tls internal`，或用 mkcert 產生憑證），轉到 `http://127.0.0.1:7860`；ListenMiao 本身只提供 http。

- 反向代理用另一個埠（例如 443 或 8443），防火牆只開那個埠；ListenMiao 的埠維持 7860、只監聽本機。
- 每支手機、平板都要安裝並信任自己產生的根憑證。iPhone、iPad 安裝描述檔後，還要到「設定 → 一般 → 關於本機 → 憑證信任設定」打開完整信任；Android 要在系統設定安裝 CA 憑證，有些瀏覽器不採用使用者自行安裝的憑證。
- 在憑證警告頁直接按「繼續前往」不是解法：連線沒有驗證對方身分，各瀏覽器對這種頁面能不能用麥克風的處理也不一致。
- 憑證綁定網址裡的 IP 或名稱；電腦的區網 IP 變了（例如 DHCP 重新分配）就要重發憑證，或改成固定 IP。
- 根憑證的私鑰要保管好：拿到它的人可以對信任它的裝置冒充任何網站。
- Caddy 轉送時保留原本的 `Host`；其他反向代理若改送自己的名稱，被拒絕時日誌會記下服務實際收到的名稱。

### 手機上操作

- 寬度不到 900 像素時改成單欄，由上往下是工具列、時間軸、字幕、工作、字幕編輯、說話者、匯出與資訊。
- 在波形上輕點會跳到該處，左右拖曳可以移動播放位置；上下滑動照常捲動頁面，不會改變播放位置。
- 匯出的檔案存到瀏覽器的下載位置（iPhone 是「檔案」app 的「下載項目」）。
- iPhone 錄音時請讓 Safari 留在前景、不要鎖定螢幕，切到其他 app 或鎖定螢幕可能讓錄音中斷。
- 辨識在電腦上進行，關掉手機的網頁後工作照樣繼續；重新開啟網頁會回到上次的工作。

## 監看資料夾

把 `.env` 的 `ASR_WATCH_DIR` 設成一個資料夾，服務開著時，放進去的錄音或影片會自動辨識，結果寫在原檔旁邊，例如 `週會.m4a` 會產生 `週會.m4a.srt`、`週會.m4a.md`。模型、說話者、標點、格式與檢查間隔由 `ASR_WATCH_*` 決定（見[設定](configuration.md#env)），熱詞與取代規則沿用網頁「詞彙」裡的設定；結果也會出現在網頁的「工作」清單，可以再校對、匯出。

- 只看資料夾本身，不看子資料夾；副檔名要是常見的音訊或影片格式，`.` 或 `~$` 開頭的暫存檔不處理。
- 檔案大小與修改時間連續兩次檢查都相同才送出，避免讀到還在複製的檔案。
- 所有格式的結果都已存在，或旁邊有 `檔名.error.txt`（無法解析或辨識失敗的原因），就不再處理；刪掉 `.error.txt` 或結果檔即可重新辨識。已存在的結果檔不會被覆蓋。
- 服務沒開時放進去的檔案，下次啟動時會補做；重新啟動時還在處理的檔案會重新辨識。

Docker 版要用 `compose.watch.yaml` 把主機資料夾掛進容器：

```dotenv
COMPOSE_FILE=compose.yaml:compose.watch.yaml
COMPOSE_PATH_SEPARATOR=:
ASR_WATCH_SOURCE=/srv/recordings
```

這時 `ASR_WATCH_DIR` 會被改成容器內的 `/watch`，即使 `.env` 填 `off` 也會啟用監看。容器以 UID 10001 執行，主機資料夾要讓它能讀寫。

## 日誌

日誌同時印在終端機（Docker 版用 `docker compose logs`），並寫進 `ASR_DATA_DIR/logs/`，關掉視窗後仍可查看：

| 檔案 | 內容 |
| --- | --- |
| `listenmiao.log` | 網頁行程：版本與生效的設定、啟動摘要；上傳與排隊、每個工作進入的階段與耗時，處理中每 5 分鐘記一行目前的階段與耗時；進度每跨過 10% 記一行（分辨說話者，以及會回報進度的 PyTorch 與遠端服務；OpenVINO 不回報辨識進度）；分辨說話者的分群統計與語者庫比對；字幕編輯、詞彙表、語者庫、燒錄字幕、監看資料夾；被拒絕的請求與原因、瀏覽器回報的錯誤、關閉時還沒完成的工作；HTTP 請求（輪詢與熱詞計數不記） |
| `inference.log` | 推論子行程：版本、關閉 OpenVINO 用量回報（只在實際改寫設定時記一行，寫不進去時記警告）、自動調校、模型下載轉換與載入、每個工作的辨識開始與完成、編譯快取清理、啟動時的例外；載入模型與辨識開始、完成時附上推論行程的記憶體與系統可用記憶體 |
| `listenmiao-crash.log`、`inference-crash.log` | 每次啟動記一行；原生程式碼當掉時寫入當下的 Python 呼叫堆疊 |
| `inference-native.log` | 推論子行程裡 OpenVINO、GPU 驅動等原生函式庫直接印出的訊息（不會再出現在終端機） |

- 每行開頭是含時區的時間、等級與行程編號（推論子行程標「推論」），例如 `2026-10-31 23:36:35.503+08:00 INFO [推論 24884] app.engine: …`。
- 等級、換檔大小與保留份數由 `ASR_LOG_LEVEL`、`ASR_LOG_FILE_MB`、`ASR_LOG_FILE_COUNT` 決定（見[設定](configuration.md#env)），舊檔從 `.log.1` 起編號；`*-crash.log` 與 `inference-native.log` 在啟動時超過大小就換成 `.1`。
- 未處理的例外會記下完整的 traceback；辨識佇列、燒錄佇列與推論事件的處理遇到未預期的錯誤時，把那一筆工作標成失敗，再繼續處理下一筆。
- 子行程（模型轉換、調校實測、分辨說話者、自動標點、燒錄字幕）失敗時記下錯誤輸出（分辨說話者保留最後 200 行，其他全部），成功時有警告也會記下最後 20 行。

關閉服務時：

- Windows 上直接關閉終端機視窗，服務會被系統直接結束，日誌只來得及記下「終端機視窗被關閉」與還沒完成的工作；要正常關閉請按 Ctrl+C。
- Docker 版的 `compose.yaml` 設了 `stop_grace_period: 15s`：`docker compose stop` 或 `down` 時，辨識中的推論子行程最多等 10 秒結束，日誌會記到「推論行程已結束」。直接用 `docker stop` 時請加 `-t 15`（`docker stop -t 15 listenmiao`），否則 Docker 的預設寬限時間過短，服務會在關閉途中被強制結束。

日誌會記上傳的檔名、工作編號與說話者編號，不記字幕內容、熱詞內容與人名。被拒絕的請求：欄位格式不符時只記欄位名稱；其他情況記錯誤訊息，可能帶有送來的模型名稱、說話者編號、語者庫編號這類識別碼。瀏覽器回報的錯誤照錯誤訊息記下（移除控制字元、每欄最多 1000 字，整個服務每分鐘最多 30 則）。MCP server 的日誌在 MCP 用戶端那邊（標準錯誤）。

## 字幕字型

匯出 ASS 與燒錄字幕用的字型由 `ASR_SUBTITLE_FONT` 與 `ASR_SUBTITLE_FONTS_DIR` 決定（見[設定](configuration.md#env)），`.env.example` 是 Windows 的微軟正黑體。Docker 映像檔內建 Noto Sans CJK，`compose.yaml` 會自動改用它；Linux 原生執行時請改成系統裡有的中文字型，例如 `Noto Sans CJK TC` 與 `/usr/share/fonts/opentype/noto`。

macOS 內建中文字型的檔案位置會隨系統版本改變，建議自己安裝一套：從 [Noto CJK 的發布頁](https://github.com/notofonts/noto-cjk/releases)下載 Noto Sans CJK TC，用「字體簿」安裝（會放進自己帳號的 `Library/Fonts`），再把 `.env` 改成：

```dotenv
ASR_SUBTITLE_FONT="Noto Sans CJK TC"
ASR_SUBTITLE_FONTS_DIR=/Users/<帳號>/Library/Fonts
```

## 資源限制與轉呼叫

Docker 部署可以只讓容器用部分 CPU 核心，也可以把某些模型轉呼叫另一個既有的 Breeze ASR 服務：

| 疊加檔 | 作用 | 需要的設定 |
| --- | --- | --- |
| `compose.cpuset.yaml` | 限定容器可用的 CPU 編號，辨識時不佔滿所有核心 | `ASR_CPUSET`（`lscpu -e` 的 CPU 欄，例如 `8-15,24-31`） |
| `compose.remote-net.yaml` | 加入既有 Docker 網路，以容器名稱連到只綁 `127.0.0.1` 的遠端服務 | `ASR_REMOTE_DOCKER_NETWORK` |

```dotenv
COMPOSE_FILE=compose.yaml:compose.remote-net.yaml:compose.cpuset.yaml
COMPOSE_PATH_SEPARATOR=:
```

轉呼叫的模型在 `models.json` 寫成 `remote`，金鑰放在 `.env`（見[設定](configuration.md#remote)）。只屬於某次部署的設定（`.env` 範本、模型清單、部署說明）可以放在 `private/` 資料夾，它已列在 `.gitignore` 與 `.dockerignore`；`ASR_MODELS_SOURCE` 指向其中的模型清單即可。

NVIDIA 卡的顯示記憶體：

- 自動調校只在預估用量放得進那張卡當下的剩餘顯示記憶體時才用 GPU，不夠就改用 CPU，不會把卡吃滿。估算方式見[自動調校](how-it-works.md#自動調校)，實測數字見[效能實測](benchmarks.md#顯示記憶體與-cuda-優化)。
- 常駐模型數依調校當下的剩餘量決定；卡上的可用顯示記憶體之後可能變少時，調校完可以把硬體設定檔的 `max_loaded_models` 改成 1（見[設定](configuration.md#硬體設定檔)）。
- 調校完成後模型就一直佔著顯示記憶體；卡上的顯示記憶體被用滿時，辨識會以 CUDA 記憶體不足失敗，這時到「硬體」改用 CPU 的設定，或在卡比較空時「重新調校」。

## 限制

- 同一時間只處理一個辨識工作，其餘排隊；燒錄字幕另外排隊，一次燒一支，可以和辨識同時進行。
- 重新啟動時還在排隊或處理中的工作會消失；已完成的結果儲存在 `ASR_DATA_DIR/results`，到 `ASR_JOB_TTL_SECONDS` 才刪除。
- 第一次準備模型需要連上 Hugging Face；之後權重都在本機快取，離線環境可設定 `HF_HUB_OFFLINE=1`。
- `.env.example` 的上限是單檔 2 GB、3 小時。近 3 小時的錄音辨識加分辨說話者約需 1 小時以上、各行程記憶體峰值合計約 8 GB；記憶體較少的電腦可以調低 `ASR_MAX_AUDIO_SECONDS`。

## GPU 支援

| 硬體 | 推論方式 | 作業系統 |
| --- | --- | --- |
| Intel 內顯、Intel Arc 獨顯 | OpenVINO | Windows 原生、Linux 原生（需安裝 Intel GPU 驅動） |
| NVIDIA GPU | PyTorch CUDA 13.0 | Windows、Linux、Docker |
| AMD GPU | PyTorch ROCm 7.2 | Linux、Docker（Linux 主機） |

- 用哪個裝置由[自動調校](how-it-works.md#自動調校)實測決定，也可以在工具列的「硬體」改用其他實測過的設定。三種方式都支援 Breeze ASR 26 的[句子分段](how-it-works.md#句子分段)。
- 已經調校過的電腦換裝 CUDA 或 ROCm 版 PyTorch 後，要到「硬體」按「重新調校」，GPU 才會納入測試。
- 原生執行、主機有多張 NVIDIA 卡時，PyTorch 預設把最快的卡排成 `cuda:0`，編號不一定和 `nvidia-smi` 相同；「硬體」的實測結果會標出卡名與 PyTorch 的編號。只想用其中一張，在 `.env` 加上 `CUDA_VISIBLE_DEVICES=GPU-<UUID>`（UUID 用 `nvidia-smi -L` 查），PyTorch 就只看得到那張卡；改了之後要到「硬體」按「重新調校」。

## 不支援或未實測的硬體

| 硬體 | 狀況 |
| --- | --- |
| Intel NPU（AI Boost） | 不支援，自動調校不測。在 Core Ultra 7 155U 上，1.5B 參數的 Whisper 編譯約 10 分鐘後，推論時驅動回報 `DEVICE_LOST`、裝置被重置（OpenVINO 2026.4.1、NPU 驅動 32.0.100.5540） |
| Docker 裡的 Intel 內顯 | Docker Desktop（WSL2）的容器看不到內顯，只能用 CPU；Linux 主機把 `/dev/dri` 交給容器理論上可用內顯，但本專案沒有提供對應的 compose 疊加檔，也未實測 |
| Windows ARM64（Snapdragon 等） | 無法安裝：OpenVINO 與 PyTorch 都沒有 Windows ARM64 版，安裝腳本與 `uv sync` 會失敗。改用 x64 版 Python（例如 `uv sync --python cpython-3.12-windows-x86_64-none`）透過 Windows 的 x64 模擬執行，理論上能用 CPU 推論，但會明顯變慢，OpenVINO 需要的 x86 指令集在模擬下能否正常運作也未實測 |
| Qualcomm Hexagon NPU、Adreno GPU | 用不到：Hexagon 需要 Qualcomm QNN，Adreno 不支援 CUDA 或 ROCm，OpenVINO 也不認得這兩種裝置 |
| Apple Silicon | 可以用[安裝腳本](#macos-安裝腳本)安裝，以 CPU（OpenVINO）推論，需要 macOS 14 以上；整套流程（安裝、模型轉換、自動調校、燒錄字幕）都還沒在實機上跑過。Apple GPU（PyTorch 的 MPS）沒有納入自動調校 |
| Intel Mac | 無法原生安裝：OpenVINO 2026 與 PyTorch 2.3 起都沒有 Intel Mac 版。改用 Docker 的 CPU 版，發布的映像檔可以直接用 |
