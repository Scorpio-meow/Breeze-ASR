## 摘要

關聯 issue：

## 變更類型

- [ ] 修正問題
- [ ] 新功能
- [ ] 文件
- [ ] 重構或其他

## 檢查

- [ ] `uv run --frozen ruff format app tests` 與 `uv run --frozen ruff check app tests` 通過
- [ ] `uv run --frozen pytest` 通過，新行為有對應的測試
- [ ] 沒有寫死數值或加上預設值，設定都來自 `.env` 或 `models.json`
- [ ] 文件與 `CHANGELOG.md` 已更新（使用者看得到的變更）
- [ ] 沒有提交含個人資料的錄音、逐字稿、聲紋或 `.env`

## 介面變更

只有改到 `static/` 時需要填寫。

- [ ] 附上淺色與深色模式的截圖
- [ ] 只用鍵盤能完成整個流程，焦點順序合理且看得見
- [ ] 新的控制項有可讀出的名稱（文字或 `aria-label`）
- [ ] 不只靠顏色傳達意思
- [ ] 新的動態效果在 `prefers-reduced-motion` 下會停用
- [ ] 文字與背景的對比至少 4.5:1
- [ ] 用螢幕閱讀器操作過一次（寫明使用的工具）
