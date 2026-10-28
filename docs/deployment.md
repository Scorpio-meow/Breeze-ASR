# 部署

| 方式 | 適用 | GPU |
| --- | --- | --- |
| [Windows 安裝腳本](#windows-安裝腳本) | 個人電腦 | Intel 內顯（OpenVINO）、NVIDIA（CUDA） |
| [Linux 安裝腳本](#linux-安裝腳本) | 個人電腦、伺服器 | Intel 內顯（OpenVINO）、NVIDIA（CUDA）、AMD（ROCm） |
| [Docker](#docker) | 伺服器 | NVIDIA（CUDA）、AMD（ROCm）；Docker Desktop 上只能用 CPU |

GPU 加速的範圍見文末的 [GPU 支援](#gpu-支援)。

## 第一次啟動

不論用哪種方式部署，第一次啟動都會：

- 下載兩個模型的原始權重（約 9 GB），需要連上 Hugging Face。
- [自動調校](how-it-works.md#自動調校)：預設模型以原始精度匯出一次，只壓縮出真正要實測的權重格式，再在各裝置上實測。在 Core i7-9700（8 GB 記憶體）上，匯出約 2 分鐘、每種格式壓縮約 1–1.5 分鐘、實測約 4 分鐘，整輪約 7–9 分鐘；期間暫時多佔約 4–7 GB 磁碟，調校完只留選中的格式。
- 把另一個模型轉成選中的格式；Breeze ASR 26 轉 int8 在同一台電腦上約 7 分鐘。

轉換期間記憶體峰值約 9.5 GB；8 GB 記憶體的電腦也能完成，但會大量使用分頁檔、比較慢。進度顯示在狀態列；預設模型準備好就能先用，之後啟動約 15 秒就緒。

磁碟用量：Hugging Face 原始權重約 8.7 GB（只在轉換時需要）、轉換後的 int8 模型約 3 GB、內顯編譯快取約 3.7 GB。

## Windows 安裝腳本

下載或 clone 本專案後，在專案根目錄的 PowerShell 執行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

腳本會安裝 [uv](https://docs.astral.sh/uv/)（還沒裝的話）、建立 Python 環境、從 `.env.example` 建立 `.env`，並在桌面建立「Breeze ASR」捷徑。偵測到 `nvidia-smi` 時裝 CUDA 版 PyTorch，否則裝 CPU 版；可以用參數指定：

| 參數 | 作用 |
| --- | --- |
| `-Device cpu`、`-Device cuda` | 指定 PyTorch 版本 |
| `-NoShortcut` | 不建立桌面捷徑 |

之後點桌面捷徑，或執行 `scripts\start.ps1` 啟動；服務就緒後會自動開啟瀏覽器（加 `-NoBrowser` 則不開），關閉視窗即停止服務。

不用腳本也可以手動執行：

```powershell
Copy-Item .env.example .env
uv sync --frozen
uv run --env-file .env python -m app
```

開啟 <http://127.0.0.1:7860>。模型權重的下載快取沿用 Hugging Face 的 `HF_HOME`（通常是 `%USERPROFILE%\.cache\huggingface`）。

## Linux 安裝腳本

在專案根目錄：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

偵測到 `nvidia-smi` 時裝 CUDA 版 PyTorch、偵測到 `rocm-smi` 或 `amd-smi` 時裝 ROCm 版，否則裝 CPU 版；可以用 `bash scripts/install.sh cpu`、`cuda` 或 `rocm` 指定。ROCm 需要主機已安裝 AMD 的 ROCm 驅動，且使用者在 `video`、`render` 群組。

## 讓其他電腦連線

- 原生執行：把 `.env` 的 `ASR_HOST` 改成 `0.0.0.0`，並確認防火牆允許該埠。
- Docker：把 `ASR_PUBLISH_ADDRESS` 改成 `0.0.0.0`。
- 服務沒有登入驗證，開放前請先看 [SECURITY.md](../SECURITY.md)。
- 瀏覽器只允許在 HTTPS 或 `localhost` 的網頁使用麥克風，其他電腦以 http 開啟時按「錄音」只會提示原因。要錄音有兩種做法：
  - 長期使用：在服務前面加一層 HTTPS 反向代理。
  - 測試或內網少數電腦：在那台電腦的 Chrome 開 `chrome://flags/#unsafely-treat-insecure-origin-as-secure`（Edge 是 `edge://flags/#unsafely-treat-insecure-origin-as-secure`），把「Insecure origins treated as secure」改成 Enabled，在文字框填入服務的完整網址（含 `http://` 與埠號，例如 `http://192.168.1.10:7860`，多個網址用逗號分隔），再按 Relaunch 重新啟動瀏覽器。這個設定只對該台電腦的瀏覽器有效，公司管理的瀏覽器可能被政策鎖住；錄音在 http 下以明文傳送，不要用在不信任的網路。

## Docker

### CPU

Docker Desktop（WSL2）的容器拿不到 Intel 內顯與 NPU，所以 Docker 版在這種環境只用 CPU，適合沒有 Intel 內顯的 Linux 伺服器：

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

- 資料（權重快取、轉換後的模型、硬體設定檔、辨識結果、語者庫）存在 `breeze-asr_asr-data` volume，重建映像檔不會重新下載或重新調校。
- 容器網段由 `ASR_DOCKER_SUBNET` 指定，預設 `192.168.202.0/24`，請確認不會和內部網路衝突。
- 轉換 Breeze ASR 26（原始權重 6 GB）時容器記憶體峰值約 6 GB，Docker Desktop 建議至少配置 8 GB（WSL2 預設為實體記憶體的一半）。

### NVIDIA GPU（CUDA）

需要 NVIDIA Container Toolkit。在 `.env` 設定：

```dotenv
COMPOSE_FILE=compose.yaml:compose.cuda.yaml
COMPOSE_PATH_SEPARATOR=:
ASR_TORCH_GROUP=cu130
ASR_IMAGE=breeze-asr-webapp:cuda
ASR_CUDA_DEVICE_ID=0
```

`ASR_CUDA_DEVICE_ID` 是 `nvidia-smi` 的 GPU 編號。映像檔約 10 GB；容器裡的自動調校會同時實測 CUDA 與 CPU。

### AMD GPU（ROCm）

只支援 Linux 主機，需要已安裝 AMD 的 ROCm 驅動。在 `.env` 設定：

```dotenv
COMPOSE_FILE=compose.yaml:compose.rocm.yaml
COMPOSE_PATH_SEPARATOR=:
ASR_TORCH_GROUP=rocm72
ASR_IMAGE=breeze-asr-webapp:rocm
ASR_VIDEO_GID=44
ASR_RENDER_GID=110
```

`ASR_VIDEO_GID`、`ASR_RENDER_GID` 是主機上 `getent group video render` 顯示的 GID，容器靠這兩個群組存取 `/dev/kfd` 與 `/dev/dri`。ROCm 版 PyTorch 映像檔很大（10 GB 以上）。不在 ROCm 官方支援清單的消費級顯示卡，可能需要在 `.env` 加上 `HSA_OVERRIDE_GFX_VERSION`。

### 使用發布的映像檔

推送 `v*` 標籤時，GitHub Actions 會建置三種映像檔並發布到 GitHub Container Registry：

| 標籤 | PyTorch |
| --- | --- |
| `ghcr.io/<owner>/<repo>:cpu` | CPU |
| `ghcr.io/<owner>/<repo>:cuda` | CUDA 13.0 |
| `ghcr.io/<owner>/<repo>:rocm` | ROCm 7.2 |

每個版本另有 `<版本>-cpu` 這類固定標籤。在 `.env` 把 `ASR_IMAGE` 設成對應的映像檔（GPU 版也照上面設定 `COMPOSE_FILE`），然後：

```bash
docker compose pull
docker compose up -d --no-build
```

## 共用主機

部署到已經有其他服務的主機時，可以只用部分 CPU 核心，並把某些模型轉呼叫主機上既有的 Breeze ASR 服務：

| 疊加檔 | 作用 | 需要的設定 |
| --- | --- | --- |
| `compose.cpuset.yaml` | 限定容器可用的 CPU 編號，辨識時不會佔滿整台主機 | `ASR_CPUSET`（`lscpu -e` 的 CPU 欄，例如 `8-15,24-31`） |
| `compose.remote-net.yaml` | 加入既有 Docker 網路，以容器名稱連到只綁 `127.0.0.1` 的遠端服務 | `ASR_REMOTE_DOCKER_NETWORK` |

```dotenv
COMPOSE_FILE=compose.yaml:compose.remote-net.yaml:compose.cpuset.yaml
COMPOSE_PATH_SEPARATOR=:
```

轉呼叫的模型在 `models.json` 寫成 `remote`，金鑰放在 `.env`（見[設定](configuration.md#remote)）。

只屬於某一台主機的設定（`.env` 範本、模型清單、部署說明）可以放在 `private/` 資料夾，它已列在 `.gitignore` 與 `.dockerignore`；`ASR_MODELS_SOURCE` 指向其中的模型清單即可。

## 限制

- 同一時間只處理一個工作，其餘排隊。重新啟動時還在排隊或處理中的工作會消失；已完成的結果保存在 `ASR_DATA_DIR/results`，到 `ASR_JOB_TTL_SECONDS` 才刪除。
- 第一次準備模型需要連上 Hugging Face；之後權重都在本機快取，離線環境可設定 `HF_HUB_OFFLINE=1`。
- `.env.example` 的上限是單檔 2 GB、3 小時。近 3 小時的錄音辨識加分辨說話者約需 1 小時以上、各行程記憶體峰值合計約 8 GB；記憶體較少的電腦可以調低 `ASR_MAX_AUDIO_SECONDS`。

## GPU 支援

| 硬體 | 推論方式 | 作業系統 |
| --- | --- | --- |
| Intel 內顯、Intel Arc 獨顯 | OpenVINO | Windows 原生、Linux 原生（需安裝 Intel GPU 驅動） |
| NVIDIA GPU | PyTorch CUDA 13.0 | Windows、Linux、Docker |
| AMD GPU | PyTorch ROCm 7.2 | Linux、Docker（Linux 主機） |

用哪個裝置由[自動調校](how-it-works.md#自動調校)實測決定，也可以在工具列的「硬體」改用其他實測過的設定。三種方式都支援 Breeze ASR 26 的[句子分段](how-it-works.md#句子分段)。已經調校過的電腦換裝 CUDA 或 ROCm 版 PyTorch 後，要到「硬體」按「重新調校」，GPU 才會納入測試。

## 不支援或未實測的硬體

| 硬體 | 狀況 |
| --- | --- |
| Intel NPU（AI Boost） | 自動調校最後才測。先前在舊版 OpenVINO 上，1.5B 參數的 Whisper 編譯約 10 分鐘後，推論時驅動回報 `DEVICE_LOST`、裝置被重置。仍失敗時只淘汰 NPU，最多多花一次 15 分鐘逾時，「硬體」的實測結果會列出原因 |
| Docker 裡的 Intel 內顯與 NPU | Docker Desktop（WSL2）的容器看不到這兩種裝置，只能用 CPU；Linux 主機把 `/dev/dri` 交給容器理論上可用內顯，但本專案沒有提供對應的 compose 疊加檔，也未實測 |
| Windows ARM64（Snapdragon 等） | 無法安裝：OpenVINO 與 PyTorch 都沒有 Windows ARM64 版，安裝腳本與 `uv sync` 會失敗。改用 x64 版 Python（例如 `uv sync --python cpython-3.12-windows-x86_64-none`）透過 Windows 的 x64 模擬執行，理論上能用 CPU 推論，但速度會明顯變慢，OpenVINO 需要的 x86 指令集在模擬下能否正常運作也未實測 |
| Qualcomm Hexagon NPU、Adreno GPU | 用不到：Hexagon 需要 Qualcomm QNN，Adreno 不支援 CUDA 或 ROCm，OpenVINO 也不認得這兩種裝置 |
| Apple Silicon | 未實測；OpenVINO 有 macOS ARM64 版，理論上能用 CPU 推論，但 Apple GPU（PyTorch 的 MPS）沒有納入自動調校 |
