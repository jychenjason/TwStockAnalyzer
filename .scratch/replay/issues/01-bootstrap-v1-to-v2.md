# 01 — Bootstrap：既有系統移植到 V2

**What to build:** V2 成為一個可獨立執行的完整專案，既有的資料抓取、分析、圖表、CLI、Streamlit 功能全部可用且測試全綠。此票不含任何 Replay 功能，純粹是把地基搬過來並確認沒有在搬運過程中摔壞。

**Blocked by:** None — can start immediately.

**Status:** done

- [x] 既有專案的程式碼、測試、設定檔、相依套件定義完整存在於 V2
- [x] 既有測試套件在 V2 目錄下全數通過
- [x] CLI 的所有既有子命令可正常執行並顯示說明
- [x] Streamlit 既有頁面可啟動
- [x] 資料庫可在 V2 建立完整 schema
- [x] 敏感設定（API token）不隨程式碼進版控
- [x] 本次移植不修改任何既有行為——測試若需修改才能通過，先停下來回報原因

## Comments

- 移植完成：既有測試 172 passed。
- 發現既有打包缺口：`pyproject.toml` 的 dev extras 從未宣告 `httpx`（starlette testclient 需要它），V1 是靠 venv 裡手動裝的套件才跑得起來。已補進 dev extras，這是移植期間唯一的既有檔案修改。
- V2 使用自己的 `.venv`，未沿用 V1 的——V1 的 venv 是 editable 安裝且指向 V1 的 src，沿用會讓 V2 的測試偷偷測到 V1 的程式碼。
- 已一併複製 `.env`（本機開發用，`.gitignore` 已排除）與 `data/twstock.db`（258,482 筆日 K）。
