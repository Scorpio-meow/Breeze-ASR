# Changelog

格式依照 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號依照 [語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

第一個版本，`pyproject.toml` 標為 0.1.0。

### Added

- 網頁 APP：上傳音訊或影片、拖進視窗或瀏覽器錄音，工作排隊並在狀態列顯示排隊位置、階段與預估時間。
- 字幕編輯軟體式的介面：工具列、波形與時間尺、說話者軌道、字幕列表、右側面板與狀態列；時間軸可點擊或拖曳定位，空白鍵播放／暫停，播放時字幕列表跟著捲動。
- 支援 Breeze ASR 25 與 Breeze ASR 26，模型清單由 `models.json` 指定並固定版本。
- 三種推論方式：本機 OpenVINO、本機 PyTorch、轉呼叫既有的 Breeze ASR 服務。
- 自動調校：第一次啟動時依 OpenVINO 裝置回報的運算精度，實測各裝置與權重格式（int4、int8、fp16、fp32），選出最快且輸出正確的推論設定；只轉換要實測的格式，放不下的格式在轉換前就略過，NPU 排在最後測、失敗只淘汰 NPU。可重新調校、在「硬體」改用其他實測過的設定，或手寫硬體設定檔。
- 排隊中與處理中的工作都能取消；工作進行中不能重複送出同一段音訊。
- 瀏覽器從其他網站送來的寫入請求一律拒絕。
- 日誌記錄下載、轉換、調校、工作與分辨說話者的開始、完成與耗時。
- Breeze ASR 26 的句子分段：用逐字時間戳把 30 秒長段落切成句子級字幕。
- 分辨說話者（自動或指定人數）與語者庫，可替說話者取名並自動認人。
- 匯出 TXT、SRT、VTT、JSON；結果保存到資料目錄，重新啟動後仍可下載。
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
