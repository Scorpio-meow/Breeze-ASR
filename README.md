# Breeze ASR 網頁 APP

在瀏覽器裡用 Breeze ASR 25／26 把語音轉成字幕，可分辨說話者，可完全在本機執行。

> 使用 MediaTek Research 釋出的 [Breeze ASR](https://huggingface.co/MediaTek-Research/Breeze-ASR-25) 模型的非官方專案，與 MediaTek 無關。

A local web app for transcribing speech with the Breeze ASR 25/26 models: waveform timeline, subtitle grid, speaker diarization with a voice library, and SRT/VTT/TXT/JSON export. Unofficial; not affiliated with MediaTek.

![工具列、波形時間軸、字幕列表與右側面板](docs/screenshot.png)

## 快速開始

Windows，在專案根目錄的 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Linux：

```bash
bash scripts/install.sh
bash scripts/start.sh
```

安裝腳本會自動安裝 [uv](https://docs.astral.sh/uv/)、依偵測到的顯示卡裝好對應的 PyTorch（CPU、CUDA 或 ROCm），Windows 另外建立桌面捷徑。啟動後開啟 <http://127.0.0.1:7860>，把音訊或影片拖進視窗（或按「開啟…」），再按「辨識」。

第一次啟動會下載模型（約 9 GB），並依這台電腦的硬體實測各種權重格式（int4、int8、fp16、fp32），挑出最快且夠準的推論設定，約需數分鐘到十幾分鐘，進度顯示在底部狀態列。之後啟動約 15 秒就緒。細節見[第一次啟動](docs/deployment.md#第一次啟動)。

用 Docker：

```bash
cp .env.example .env
docker compose up -d --build
```

NVIDIA、AMD 顯示卡與 GitHub 發布的映像檔見[部署](docs/deployment.md)。

## 功能

- 上傳音訊或影片、拖進視窗，或用瀏覽器錄音
- 排隊顯示前面還有幾個工作，辨識中顯示預估時間；排隊中或處理中的工作都能取消
- 波形與時間軸：點、拖曳或用方向鍵定位，空白鍵播放／暫停，播放時字幕列表跟著捲動；超過 30 分鐘的音訊在送出辨識後由伺服器畫出波形
- 字幕列表與全文檢視，點一列就從那裡播放；字幕文字可以直接在列表上修改，下載的檔案會用修改後的文字
- 匯出 SRT、VTT、TXT、JSON
- 分辨說話者（自動或指定人數），替說話者取名後存進語者庫，之後的錄音自動認出這個人
- 第一次啟動時實測各裝置與權重格式，自動選用 OpenVINO（CPU、Intel 內顯）或 PyTorch（NVIDIA CUDA、AMD ROCm）；也能在「硬體」改用其他實測過的設定
- 也可以把模型轉呼叫既有的 Breeze ASR 服務

## 支援的模型

| 模型 | 適用情境 |
| --- | --- |
| [Breeze ASR 25](https://huggingface.co/MediaTek-Research/Breeze-ASR-25) | 台灣華語、中英夾雜，時間戳適合字幕 |
| [Breeze ASR 26](https://huggingface.co/MediaTek-Research/Breeze-ASR-26) | 台語（台灣閩南語），輸出華語漢字 |

其他 Whisper 架構的模型在 `models.json` 加一筆即可使用。

## 系統需求

| 項目 | 需求 |
| --- | --- |
| 作業系統 | Windows（x64）、Linux，或任何能跑 Docker 的系統；Windows ARM64（Snapdragon 等）無法安裝，見[不支援或未實測的硬體](docs/deployment.md#不支援或未實測的硬體) |
| Python | 3.12（uv 會自動下載） |
| 記憶體 | 建議 16 GB 以上；第一次轉換模型時峰值約 9.5 GB，8 GB 也能完成但會大量使用分頁檔 |
| 磁碟 | 約 16 GB（原始權重、轉換後的模型、編譯快取）；第一次自動調校期間暫時多約 4–7 GB |
| 加速 | Intel 內顯（OpenVINO）、NVIDIA GPU（CUDA）、AMD GPU（ROCm，僅 Linux），沒有也能用 CPU；Intel NPU 會納入自動調校，但先前實測無法使用；Qualcomm 的 NPU、GPU 用不到。支援範圍見[部署](docs/deployment.md#gpu-支援) |

## 文件

| 文件 | 內容 |
| --- | --- |
| [部署](docs/deployment.md) | 安裝腳本、Docker（CPU、CUDA、ROCm）、發布的映像檔、共用主機、GPU 支援 |
| [設定](docs/configuration.md) | `.env`、`models.json`、硬體設定檔 |
| [運作方式](docs/how-it-works.md) | 架構、自動調校、句子分段、分辨說話者與語者庫 |
| [API](docs/api.md) | 各端點的參數與回應 |
| [效能實測](docs/benchmarks.md) | 辨識速度、句子分段準確度、分辨說話者正確率 |
| [CHANGELOG](CHANGELOG.md) | 更新紀錄 |

## 安全性

服務沒有登入驗證，預設只監聽 `127.0.0.1`。要讓其他電腦連線前請先看 [SECURITY.md](SECURITY.md)。聲紋屬於個人生物特徵，只存在本機的資料目錄。

## 無障礙

介面以 WCAG 2.2 AA 為目標，主要工作只用鍵盤就能完成。支援的環境、已知限制與回報方式見 [ACCESSIBILITY.md](ACCESSIBILITY.md)。

## 參與

歡迎回報問題、提出建議與送 Pull Request。開 issue 時請選對應的範本：問題回報、功能建議或無障礙問題。

```powershell
uv sync --frozen --group dev
uv run --frozen pytest
uv run --frozen ruff check app tests
```

| 文件 | 內容 |
| --- | --- |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 開發環境、提交前檢查、撰寫原則 |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | 行為準則與檢舉方式 |
| [SECURITY.md](SECURITY.md) | 回報安全性問題 |
| [ACCESSIBILITY.md](ACCESSIBILITY.md) | 無障礙聲明 |

## 授權

程式碼以 [MIT](LICENSE) 授權釋出。模型、範例語音與參考的程式碼各有授權，見 [NOTICE.md](NOTICE.md)；模型都是執行時從 Hugging Face 下載，不隨本專案散布。
