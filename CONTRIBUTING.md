# 參與開發

歡迎回報問題與送 Pull Request。參與本專案即表示同意遵守[行為準則](CODE_OF_CONDUCT.md)。

## 建立開發環境

需要 [uv](https://docs.astral.sh/uv/) 與 Python 3.12（uv 會自動下載）。

```powershell
Copy-Item .env.example .env
uv sync --frozen --group dev
uv run --env-file .env python -m app
```

NVIDIA GPU 環境改用 `uv sync --frozen --no-default-groups --group cu130 --group dev`，AMD GPU（Linux）改用 `--group rocm72`。

## 提交前

```powershell
uv run --frozen ruff format app tests
uv run --frozen ruff check app tests
uv run --frozen pytest
```

GitHub Actions 會在 Ubuntu 與 Windows 上跑同樣的檢查，三項都通過才會合併。

## 撰寫原則

- 所有設定都來自 `.env` 或 `models.json`，缺少時明確報錯；不要在程式碼裡寫死數值或加上預設值。
- 程式碼不寫註解，用清楚的命名與結構表達意圖；需要說明的設計取捨寫進 `docs/`。
- 新功能盡量附上不需要下載模型的單元測試，放在 `tests/`。
- 介面維持字幕編輯軟體的風格：工具列、時間軸、字幕列表、右側面板、狀態列；狀態訊息放在狀態列，不在頁面上加說明文字。
- 前端是純 HTML／CSS／JavaScript，不引入建置工具。
- 檔案一律 UTF-8、LF 換行（`.gitattributes` 已設定）。

## 回報問題

開 issue 時請選對應的範本：

| 範本 | 用途 |
| --- | --- |
| 問題回報 | 辨識失敗、介面錯誤、安裝或部署問題 |
| 功能建議 | 新功能或改進現有功能 |
| 無障礙問題 | 無法用鍵盤、螢幕閱讀器或其他輔助科技操作，或難以閱讀（見 [ACCESSIBILITY.md](ACCESSIBILITY.md)） |

請不要附上含個人資料的錄音、逐字稿或 `data/voices/` 的聲紋檔。安全性問題請依 [SECURITY.md](SECURITY.md) 私下回報。

## Pull Request

送出時請完成 [PR 範本](.github/pull_request_template.md) 的檢查清單；改到 `static/` 的介面變更，另外完成其中的無障礙檢查。

## 授權

送出的貢獻以本專案的 [MIT 授權](LICENSE) 釋出。
