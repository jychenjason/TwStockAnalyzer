# 03 — Replay 服務層：Session 與 Cursor

**What to build:** 系統能以一檔股票與一個起始日開啟一條 Replay Session 的時間軸，並逐日推進 Cursor，任何時刻只取得 Cursor 當日及之前的資料。Session 存進資料庫，關閉後可再載入。此票不含任何畫面——驗證方式是測試與服務層介面本身。

這是整個功能的地基：Point-in-Time 這條規則在此處被建立，後續所有票都依賴它是對的。

**Blocked by:** 01

**Status:** done

- [x] 能以股票、起始日、初始資金、手續費折數建立一個 Replay Session
- [x] 建立時檢查 Coverage，資料不足時拒絕建立，並回報缺少的範圍與應執行的回補指令
- [x] 取得 Cursor 當日資料時，回傳結果不含任何晚於 Cursor 的日期
- [x] Cursor 當日的 K 棒資料完整（開高低收皆有）
- [x] Warm-up：起始日之前的本地資料全部載入，可供指標計算使用
- [x] Cursor 推進時只走該股票自身有資料的交易日，停牌日不佔格
- [x] Cursor 推進到資料末端時停止並回報已到盡頭，不拋出例外
- [x] Session 狀態（含 Cursor 位置）存入資料庫，重新載入後與存檔前完全一致
- [x] 服務層不匯入任何介面框架、不發出任何網路請求
- [x] 測試以隔離的臨時資料庫與人造價格序列驅動，價格序列須含停牌缺口

## Comments

- 以 TDD 完成，10 個紅→綠迴圈，`tests/test_replay.py` 共 14 個測試，全套 186 passed。
- 接縫維持單一：所有測試都打服務層公開介面（`create_session` / `load_session` / `advance` / `visible_prices` / `at_end`），沒有任何測試碰內部函式或繞過介面直接查資料庫。
- 測試資料刻意含一週停牌缺口（2025-09-08..12），停牌跳格與起始日 snap-forward 兩條行為因此有真實的驗證對象。
- 一條測試（Warm-up 資料可用）沒有先紅——cycle 5 的實作已滿足它，它是特徵化測試而非驅動測試，保留是因為 Warm-up 是獨立的規格條文。
- 起始日不是交易日時 Cursor 往後 snap 到下一個交易日，此行為原票未明說，實作時補上並以測試固定。
