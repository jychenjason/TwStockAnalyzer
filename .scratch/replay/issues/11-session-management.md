# 11 — Session 管理：續玩與命名

**What to build:** 使用者關掉瀏覽器後回來，能看到自己未完成的 Replay Session 清單並從原本的 Cursor 繼續；能替 Session 命名以認出每次練習的意圖；也能對同一檔股票、同一起始日開啟多個 Session，用不同做法練第二次、第三次。

**Blocked by:** 07

**Status:** done

- [x] 頁面列出既有 Session，顯示股票、起始日、目前 Cursor、名稱
- [x] 選擇既有 Session 後從原本的 Cursor 與帳戶狀態繼續，交易紀錄完整保留
- [x] Session 有自動產生的預設名稱，且可自行更名
- [x] 同一股票與同一起始日可存在多個 Session，彼此互不覆蓋
- [x] 可刪除不要的 Session，刪除前確認
- [x] 測試驗證存檔後載入的 Cursor、現金、Position、交易紀錄與存檔前一致

## Comments

- 服務層新增 `list_sessions()` / `delete_session()` / `rename()`；Session 有預設名稱「股票 @ 起始日」。
- 同一股票同一起始日可開多個 Session，彼此不覆蓋。
- 頁面提供續玩、改名、刪除。
