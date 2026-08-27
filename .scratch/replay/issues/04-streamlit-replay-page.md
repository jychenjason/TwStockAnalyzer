# 04 — 回放頁面：K 線逐日長出來

**What to build:** 使用者在網頁介面選一檔股票與一個起始日，按下開始，看到一張只畫到 Cursor 當日的 K 線圖；按「下一日」，圖上長出一根新的 K 棒。這是第一個使用者真正看得到、摸得到 Replay 的版本。

**Blocked by:** 03

**Status:** done

- [x] 頁面可選擇股票與起始日並建立 Replay Session
- [x] Coverage 不足時頁面顯示明確訊息與應執行的回補指令，而非空白或錯誤堆疊
- [x] K 線圖只呈現至 Cursor 當日，右側沒有任何未來資料
- [x] 按「下一日」後圖上新增一根 K 棒，且該日資訊完整
- [x] 圖表重用既有的 K 線繪圖能力，僅增加截斷參數，不另寫一套繪圖
- [x] 頁面不含業務邏輯——所有取數與推進都經由服務層
- [x] 未建立任何 Session 時頁面顯示引導訊息而非錯誤
- [x] 沿用既有做法以靜態檢查驗證頁面確實透過服務層取數

## Comments

- 新增 `streamlit_app/replay_page.py`，並掛進既有 app 的側邊欄（🎬 回放練習）。
- 頁面不含任何業務邏輯：`tests/test_replay_page.py` 以靜態檢查守住這件事——頁面不得出現 sqlite3 或 SQL，且必須透過服務層取數。
- 以 headless streamlit 實測服務 HTTP 200、log 無錯誤。
