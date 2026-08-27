# 13 — CLI 唯讀指令

**What to build:** 使用者能在終端機列出所有 Replay Session、檢視單一 Session 的績效摘要、把交易明細匯出成 CSV 以便在試算表裡比較多次練習。事後檢討在終端機比在網頁快得多。

CLI **不**提供互動式回放——那在 Q3 已經否決過。

**Blocked by:** 12

**Status:** done

- [x] 有指令可列出所有 Session，顯示股票、期間、Cursor、名稱、狀態
- [x] 有指令可檢視單一 Session 的績效摘要與 Buy & Hold 對照
- [x] 有指令可把單一 Session 的交易明細匯出為 CSV，含下單日、成交日、方向、張數、成交價、手續費、稅、損益、作廢原因
- [x] 指定不存在的 Session 時給出清楚錯誤並以非零離開碼結束
- [x] 所有指令皆為唯讀，不修改任何 Session
- [x] 沿用既有的 CLI 測試模式驗證離開碼與輸出內容

## Comments

- 新增 `replay list` / `replay show` / `replay export`，沿用既有 CliRunner 測試模式。
- 有一條測試明確驗證這些指令不會移動 Cursor（唯讀）。
- 找不到的 Session 以非零離開碼與清楚訊息結束。
