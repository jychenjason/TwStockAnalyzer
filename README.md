# TwStockAnalyzer

Taiwan Stock Analyzer — a CLI and web application for fetching, analyzing, and visualizing Taiwan stock data.

## Project Overview

TwStockAnalyzer provides:

- **Data fetching** from FinMind and TWSE sources with retry logic and caching.
- **Technical analysis** — SMA, MACD, RSI, KD, Bollinger Bands.
- **Fundamental analysis** — 每季 EPS（累計）、每月營收與年增率、TWSE 公布的本益比與殖利率。
- **Institutional investor tracking** — foreign/margin trading data.
- **Stock screener** — filter stocks by moving-average crossovers (MA5×MA10 / MA5×MA20, golden or death), RSI, PE ratio, 20 日均量, 每股盈餘, 月營收年增率, and more.
- **Portfolio backtesting** — multi-asset simulation with signal-based trading and performance metrics.
- **REST API** — FastAPI server exposing all analysis data via JSON endpoints.
- **CLI interface** built with Typer + Rich for terminal use.
- **Streamlit dashboard** for interactive visualization.
- **Replay (回放練習)** — step through history one trading day at a time with no lookahead, placing simulated orders with real Taiwanese trading costs.
- **SQLite-backed storage** for offline analysis.

## Installation

### Option 1: pip (local install)

```bash
# Clone the repo
git clone <repo-url>
cd TwStockAnalyzer

# Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Copy environment file and set up API tokens
cp .env.example .env
# Edit .env and add your FinMind API token
```

### Option 2: Docker (multi-service)

```bash
# .env must exist before any compose command — every service declares
# `env_file: .env`, and compose aborts if the file is missing.
cp .env.example .env

# Build the image (all three services share the twstock-analyzer:latest tag)
docker compose build

# Start the API (8000) and the Streamlit dashboard (8501)
docker compose up

# ...or just one of them
docker compose up streamlit
docker compose up -d api

# Run CLI commands (one-shot container, removed on exit)
docker compose run --rm cli --help
docker compose run --rm cli update --stock 2330 --start 2015-01-01
```

Prerequisites: Docker 20.10+, Docker Compose v2+.

## Configuration

Copy `.env.example` to `.env` and fill in your API tokens:

```bash
cp .env.example .env
```

### Environment Variables

| Variable           | Description              | Required |
|--------------------|--------------------------|----------|
| `FINMIND_API_TOKEN`| FinMind API access token | No*      |

\* FinMind is optional — the app falls back to TWSE source when no token is set.

## CLI Commands

Run `python -m twstock_analyzer.cli.main --help` for the full command list (or `tw-stock-analyzer --help` when installed).

### update

Fetch stock data from online sources and cache it in the local SQLite database.

**Cache & Incremental Update Logic:**

Daily/institutional updates use **incremental** fetch — if data exists in the DB but is not yet up to today, only the missing days are fetched (no full re-download). When `--force` is passed, all 12 months are re-fetched from scratch.

`--start YYYY-MM-DD` backfills further back than the default one-year window. When the requested start is earlier than what the cache holds, the incremental tail logic is bypassed so the older range is actually fetched rather than silently skipped. Backfill only the handful of stocks you intend to replay — the free FinMind tier allows 600 requests/hour, and a whole-market multi-year backfill would take hours and bloat the database.

Fundamental updates for `existing` / `full` commands use **batch** mode — a single API call fetches PE ratio and dividend yield for **all** stocks at once, rather than one call per stock.

```bash
# Update a single stock (daily price data) — incremental if stale
tw-stock-analyzer update --stock 2330

# Update with verbose logging
tw-stock-analyzer -v update --stock 2330

# Force re-fetch all 12 months (bypass cache)
tw-stock-analyzer update --stock 2330 --force

# Backfill years of history (required before replaying older periods)
tw-stock-analyzer update --stock 2330 --start 2015-01-01
tw-stock-analyzer update --stock 2330 --type institutional --start 2015-01-01

# Fetch ex-dividend dates (used for the markers on replay charts)
tw-stock-analyzer update --stock 2330 --type dividend --start 2015-01-01

# 抓取股票代號與公司簡稱對照表（篩選結果要顯示中文名稱前先跑一次）
tw-stock-analyzer update stocks

# Update institutional investor data for a single stock
tw-stock-analyzer update --stock 2330 --type institutional

# Update all stocks already in the database (incremental daily)
tw-stock-analyzer update existing

# Force-refresh all existing stocks
tw-stock-analyzer update existing --force

# Fetch and update ALL stocks listed on the TWSE exchange (from scratch)
tw-stock-analyzer update full

# Full update with fundamental data (batch: 1 API call for all stocks)
tw-stock-analyzer update full --type fundamental

# 每股盈餘與營業收入（季報，一次呼叫抓全市場）
tw-stock-analyzer update existing --type financials

# 月營收與年增率（一次呼叫抓全市場）
tw-stock-analyzer update existing --type revenue
```

`fundamental`、`financials`、`revenue` 是**三種不同頻率**的資料，必須分別抓：本益比與殖利率每日更新、每股盈餘每季公布、營收年增率每月公布。三者都是單次 API 呼叫涵蓋全市場，與 `--stock` 指定幾檔無關。

這兩支端點只回傳**最新一期**，沒有歷史。要累積 EPS 與月營收的走勢，得每月／每季固定執行一次 `update`；剛裝好的資料庫各只會有一個時間點，因此 EPS 走勢圖與月營收環比要跑一段時間後才有內容。

#### update --stock (單一股票)

Options:
- `--stock, -s <ID>` — Stock ID (4 digits, e.g. `2330`)
- `--type, -t <type>` — Data type: `daily`, `fundamental`, `financials`（季報 EPS／營收）, `revenue`（月營收年增率）, `institutional`, `dividend` (default: `daily`)
- `--start <date>` — Backfill from this date (YYYY-MM-DD; default: one year ago)
- `--force, -f` — Force re-fetch, bypass cache

**Cache behavior (no `--force`):**
- Latest DB date ≥ today → `CACHE_HIT`, skipped
- Latest DB date < today → `INC_HIT`, fetch only days after the latest DB date

#### update stocks

Fetches the TWSE listing and stores each stock's **公司簡稱** locally, so screening output can show names instead of a wall of 4-digit codes.

```bash
tw-stock-analyzer update stocks
```

Names are also refreshed automatically whenever `update --stock all` / `update full` runs, since those already fetch the listing. Stocks without a stored name still appear in results — the name column simply shows `-`.

#### update existing

Updates all stocks that already exist in the local database.

```bash
# Incremental daily update for all stocks in DB
tw-stock-analyzer update existing

# Update institutional data for all stocks
tw-stock-analyzer update existing --type institutional

# Force re-fetch all 12 months for every stock
tw-stock-analyzer update existing --force

# Fundamental data for all stocks (batch: 1 API call)
tw-stock-analyzer update existing --type fundamental
```

Options:
- `--type, -t <type>` — Data type: `daily`, `fundamental`, `financials`, `revenue`, `institutional` (default: `daily`)
- `--force, -f` — Force re-fetch, bypass cache (daily/institutional only)
- `--start <date>` — Backfill from this date (YYYY-MM-DD)

`update existing` 與 `update --stock existing` 是同一件事，`update full` 與 `update --stock all` 也是——兩種寫法走同一段程式碼。

Shows progress (`Updating 1/3: 2330`), skips up-to-date stocks, and reports a final summary. Individual stock failures don't abort the batch.

「已是最新」的判斷基準是**可得最新交易日**，不是日曆上的今天：下午四點以前來源只公布到前一個交易日，週末與假日則完全沒有新資料。追上了就連請求都不會發出（log 顯示 `Cache hit`），而不是抓回一張空表再說「已是最新」。細節見 [「已是最新」比的是可得最新交易日，不是今天](#已是最新比的是可得最新交易日不是今天)。

#### update full

Fetches the complete list of all listed stocks from the TWSE OpenAPI, then updates each one. This is the recommended way to seed an empty database for the first time.

```bash
# First-time setup: fetch all ~987 TWSE-listed stocks
tw-stock-analyzer update full

# Full update with verbose logging
tw-stock-analyzer -v update full

# Full update with fundamental data (batch: 1 API call)
tw-stock-analyzer update full --type fundamental
```

Options:
- `--type, -t <type>` — Data type: `daily`, `fundamental`, `financials`, `revenue`, `institutional` (default: `daily`)
- `--force, -f` — Force re-fetch, bypass cache (daily/institutional only; fundamental always uses batch)

Shows stock names alongside IDs during progress (`Updating 1/987: 1101 (台泥)`) and reports a final summary when complete.

### analyze

Run technical analysis on a stock with configurable indicators.

```bash
# Analyze with default indicators (MA, RSI)
tw-stock-analyzer analyze 2330

# Custom indicators
tw-stock-analyzer analyze 2330 --indicators macd,rsi,kd,bb

# Date range
tw-stock-analyzer analyze 2330 --start 2024-01-01 --end 2024-12-31

# JSON output
tw-stock-analyzer analyze 2330 --output json

# Verbose
tw-stock-analyzer -v analyze 2330 --indicators ma,macd,rsi
```

Options:
- `stock_id` — Stock ID (4 digits)
- `--indicators, -i <list>` — Comma-separated: `ma`, `macd`, `rsi`, `kd`, `bb` (default: `ma,rsi`)
- `--start <date>` — Start date YYYY-MM-DD (default: `2024-01-01`)
- `--end <date>` — End date YYYY-MM-DD (default: today)
- `--output, -o <format>` — Output format: `table`, `json` (default: `table`)

Available indicators:

| Indicator | Description                             |
|-----------|-----------------------------------------|
| `ma`      | Simple Moving Average (5/10/20/60/120/240) |
| `macd`    | Moving Average Convergence Divergence   |
| `rsi`     | Relative Strength Index (14)            |
| `kd`      | KDJ Indicator                           |
| `bb`      | Bollinger Bands (20, 2)                 |

### report

Generate analysis reports in HTML or JSON format.

```bash
# HTML report (default)
tw-stock-analyzer report 2330 --format html

# JSON report (immediate)
tw-stock-analyzer report 2330 --format json

# Save to file
tw-stock-analyzer report 2330 --format json --output report.json

# Output to stdout
tw-stock-analyzer report 2330 --format json --output -
```

Options:
- `stock_id` — Stock ID (4 digits)
- `--format, -f <format>` — Report format: `html`, `json` (default: `html`)
- `--output, -o <path>` — Output file path (default: `report-{stock_id}.html`)

HTML reports include interactive Plotly charts with price, volume, and technical indicators.

### list

List stocks currently in the database with basic overview.

```bash
# Show top 20 stocks by closing price
tw-stock-analyzer list

# Show top 10
tw-stock-analyzer list --top 10
```

Options:
- `--top, -t <N>` — Number of stocks to display (default: `20`)

### screen

Screen stocks by fundamental and technical criteria.

```bash
# Run a screen with PE ratio range and RSI filter
tw-stock-analyzer screen run --pe-min 10 --pe-max 20 --rsi-max 30

# Screen with volume and EPS filters
tw-stock-analyzer screen run --volume-min 1000000 --eps-min 5

# 篩出最新一季 EPS 5 元以上、且最新月營收年增率超過 30% 的股票
tw-stock-analyzer screen run --eps-min 5 --revenue-yoy-min 30

# 爆量：最新交易日成交量達前 20 日均量的 2 倍以上
tw-stock-analyzer screen run --volume-spike 2

# 爆量 + 排除沒量的小型股（強烈建議）
tw-stock-analyzer screen run --volume-spike 2 --volume-min 500000

# 爆量上漲（承接）
tw-stock-analyzer screen run --volume-spike 2 --volume-min 500000 --spike-direction up

# 爆量下跌（出貨）
tw-stock-analyzer screen run --volume-spike 2 --volume-min 500000 --spike-direction down

# 均線交叉：MA5 今天由下而上穿越 MA10（黃金交叉）
tw-stock-analyzer screen run --ma-cross 5x10

# 最近 5 個交易日內 MA5 穿越 MA20
tw-stock-analyzer screen run --ma-cross 5x20 --ma-within 5

# 死亡交叉：MA5 由上而下跌破 MA10
tw-stock-analyzer screen run --ma-cross 5x10 --ma-direction down --ma-within 5

# 交叉條件可與其他條件並用
tw-stock-analyzer screen run --ma-cross 5x10 --ma-within 5 --rsi-max 70 --volume-min 1000000

# 只看交叉本身，不要求兩條均線同方向（回到舊行為）
tw-stock-analyzer screen run --ma-cross 5x20 --ma-within 5 --no-ma-trend-align

# Run and save the criteria for later reuse
tw-stock-analyzer screen run --pe-min 10 --pe-max 20 --name "value-stocks"

# List saved screening criteria
tw-stock-analyzer screen list

# Run a saved screening criteria by ID
tw-stock-analyzer screen show 1

# Delete saved criteria
tw-stock-analyzer screen delete 1
```

交叉的定義是**事件而非狀態**：前一日快線不在慢線之上、當日在其之上才算穿越。已經維持多頭排列一個月的股票不會被列入——它今天並沒有「穿越」。上市未滿慢線期數的股票會被略過，而不是用不完整的均線硬算。

交叉還必須**趨勢同方向**（見下節）：黃金交叉要求交叉當日快線與慢線都在上彎，死亡交叉則都在下彎。加 `--no-ma-trend-align` 可關掉。

> **行為變更**：`--ma-cross` 現在預設要求趨勢同方向，**同樣的指令會比過去篩出更少的股票**（實測全市場約少一半）。要拿到舊結果請加 `--no-ma-trend-align`。

> **行為變更**：`--rsi-min` / `--rsi-max` 過去雖然被 CLI 與 REST API 接受，但 `run_screen` 從未套用，回傳的是一份未經 RSI 篩選的清單。現已實際生效（以最新一根 K 棒的 RSI(14) 判斷），因此**同樣的指令現在會得到與過去不同、且正確的結果**。

篩選結果的每一欄都取自**最新一筆**資料：`Close` 是最新交易日的收盤價、`20日均量` 是最近 20 個交易日的平均成交量、基本面各欄取最新一期報告。`--pe-min` / `--pe-max` 判斷的也是最新一期，`--volume-min` 判斷的是 20 日均量而非單日成交量。

篩選結果會一併顯示公司簡稱。名稱來自本地的 `stocks` 對照表，若尚未建立請先執行 `tw-stock-analyzer update stocks`；沒有名稱的股票仍會被列出，名稱欄顯示 `-`。

Criteria options for `screen run`:

| Option                 | Description                        |
|------------------------|------------------------------------|
| `--pe-min`             | Minimum PE ratio                   |
| `--pe-max`             | Maximum PE ratio                   |
| `--rsi-min`            | Minimum RSI                        |
| `--rsi-max`            | Maximum RSI                        |
| `--volume-min`         | 最低 20 日均量（不是單日成交量）    |
| `--volume-spike`       | 爆量倍數：當日量 ÷ 前 N 日均量（建議 2.0） |
| `--volume-spike-window`| 爆量基準的取樣天數，不含當日（預設 20） |
| `--spike-direction`    | `up` = 爆量上漲（承接），`down` = 爆量下跌（出貨）|
| `--spike-direction-band`| 方向判定的中性帶 %（預設 1.0；0 = 只看正負號）|
| `--ma-cross`           | 均線交叉，例如 `5x10`、`5x20`（也接受 5/10、5-10） |
| `--ma-direction`       | `up` = 黃金交叉（預設），`down` = 死亡交叉 |
| `--ma-within`          | 交叉需發生在最近 N 個交易日內（預設 1 = 今天） |
| `--ma-trend-align` / `--no-ma-trend-align` | 交叉當日兩條均線是否須同方向（預設開啟） |
| `--ma-trend-window`    | 判斷均線方向時回看的交易日數（預設 3） |
| `--eps-min`            | 最低每股盈餘（最新一季，累計至該季） |
| `--eps-growth-min`     | `--eps-min` 的舊名，行為相同        |
| `--revenue-yoy-min`    | 最低月營收年增率 %（最新一個月）     |
| `--dividend-yield-min` | Minimum dividend yield (%)         |
| `--name, -n`           | Save criteria with this name       |

#### 均線交叉的趨勢同方向（`--ma-trend-align`）

一筆「MA5 上穿 MA20」可能是兩件完全相反的事：

- **趨勢轉多** — MA20 自己已經翻上來，MA5 順勢穿越。
- **跌勢中的反彈** — MA20 還在下墜，MA5 只是反彈得比它快而撞了上去。

只看交叉，兩者長得一模一樣。`--ma-trend-align`（預設開啟）要求**交叉當日**兩條均線都朝交叉的方向走，把後者排除：

| 方向 | 條件 |
|------|------|
| `--ma-direction up`（黃金交叉） | 交叉當日 MA 快線與慢線都比 3 個交易日前**高** |
| `--ma-direction down`（死亡交叉） | 交叉當日 MA 快線與慢線都比 3 個交易日前**低** |

真正在篩的是**慢線**。實測 1,089 檔上市股一年份的 5x20 交叉，快線幾乎不篩掉東西（97.6% 的黃金交叉本來就滿足——快線要穿上去多半得自己上彎），慢線則只有約 54% 通過。整體通過率約 43%。

**趨勢一律看交叉當日，不是最新一日。** 這樣一筆交叉合不合格由事件本身決定；昨天篩得到的今天不會只因為 `--ma-within` 往後挪一格就消失。

**回看天數預設 3。** MA20 的單日斜率等於 `(今日收盤 - 20 日前收盤) ÷ 20`，只要一根 K 棒進出視窗就會翻面——實測 MA20 單日斜率的正負號有 12.3% 的日子隔天就反轉，回看 3 日降到 6.9%。不取 5 是因為翻面率只再降到 5.3%，通過率卻從 43% 掉到 29%。斜率恰好為 0 不算同方向：走平不是趨勢。

> **這是選擇性條件，不是績效條件。** 以本地一年份資料回測 5x20 交叉後 10 個交易日的報酬，通過與被濾掉的兩組並沒有顯著差距（黃金交叉勝率 42.0% vs 41.0%）。它的作用是把清單收斂到「慢線也在轉向」的那一半，不是保證選出的股票會漲。

#### 爆量（`--volume-spike`）

判斷**最新交易日**的成交量是否顯著高於平時：`當日成交量 ÷ 前 20 日均量 >= 倍數`。

**基準不含當日**，這點很重要：把當日算進均量，爆得越兇被自己稀釋得越多（前 19 天量 1.0、今天 3.0，含當日只會量到 2.74 倍），而且稀釋是非線性的。

參數的選擇基於實際市場分布（1,081 檔滿 61 個交易日的上市股）：

| 窗口 | 量比中位數 | 90% | 95% | 99% |
|---|---|---|---|---|
| 5 日 | 0.80 | 1.76 | 2.61 | 8.64 |
| **20 日** | **0.69** | **1.61** | **2.22** | **5.15** |
| 60 日 | 0.45 | 1.45 | 1.94 | 4.64 |

取 **20 日**：5 日均量會被連續放量自己墊高，真正的爆量反而測不出來，尾部也最亂；60 日的量比中位數只有 0.45，基準被舊的量能水位污染，會把「量能回到正常」誤判成爆量。20 日等於一個交易月。

取 **2.0 倍**：約落在分布的第 93 百分位，選出約 6.9% 的股票（實測 75 檔）——統計上確實異常，數量也還看得完。1.5 倍會選出 12.1%，那只是「高於平均」；3.0 倍只剩 3.1%，會漏掉真實的進貨。

| 倍數 | 選出檔數（20 日基準）|
|---|---|
| 1.5 | 131 檔 (12.1%) |
| **2.0** | **75 檔 (6.9%)** |
| 3.0 | 34 檔 (3.1%) |

**低量股會污染整份清單**：2 倍以上的 75 檔裡有 33 檔 20 日均量不到 500 張，倍數榜前排是「均量 6 張、當日 37 張 = 5.7 倍」這種東西。系統**不會**偷偷濾掉它們，而是在沒給 `--volume-min` 時明講有幾檔低於門檻。實務上建議固定加上 `--volume-min 500000`（500 張）。

**爆量沒有方向**：實測 2 倍以上的 74 檔中上漲 35 檔、中性 16 檔、下跌 23 檔。爆量上漲（承接）與爆量下跌（出貨）是相反的訊號，所以用了 `--volume-spike` 時輸出會多出 `量比` 與 `漲跌%` 兩欄，並可用 `--spike-direction up|down` 直接分開。

中性帶預設 **±1%**：全市場當日 |漲跌幅| 的中位數是 1.42%，1% 落在典型單日波動之下，所以被歸為中性的確實是「沒走到哪裡去」的那些。

| 中性帶 | 上漲 | 中性 | 下跌 |
|---|---|---|---|
| ±0.0 | 43 | 3 | 28 |
| **±1.0** | **35** | **16** | **23** |
| ±3.0 | 21 | 42 | 11 |

決定這個預設值的是 `2455 全新`：**3.79 倍量卻只有 +0.12%**。三倍多的量而股價沒動是換手攻防，不是承接；用純正負號（`--spike-direction-band 0`）會把它算成爆量上漲。

`--spike-direction` 必須搭配 `--volume-spike` 使用，單獨給會直接報錯而不是默默變成一個漲跌幅篩選。

**沒跟上最新交易日的股票會被排除**，並依原因分開報告：

| 情況 | 訊息 | 該怎麼辦 |
|---|---|---|
| 資料只落後 1~2 個交易日 | ⚠ `有 N 檔的日線尚未更新到 …` | `update existing` 沒跑完，跑完再篩 |
| 長期沒有新資料 | `已排除 N 檔停止交易的股票` | 市場常態，不必處理 |

兩者的後果完全不同：後者是市場上本來就有的幾檔下市／停牌股；前者代表**你的篩選少掉了一整塊市場**。當被排除的比例超過 5%，還會再警告一次涵蓋範圍：

```
⚠ 本次爆量篩選僅涵蓋 918/1089 檔（84%），結果並不完整——
   沒涵蓋到的股票是完全沒參與比對，不是比了沒中。
```

#### 更新失敗不會偽裝成「已是最新」

TWSE 的 `STOCK_DAY` 對連續請求回 **HTTP 428** 限流。一次全市場更新裡，這種失敗曾佔到四成的請求。

失敗的處理方式決定了它會不會被發現：

- 抓不到資料時退回 `STOCK_DAY_ALL` 快照。這個快照只有最新一天，而且常落後一天——要 08-20 它只給得出 08-19，落在區間外就變成 0 筆。
- 0 筆會被呼叫端讀成「今天沒有新資料」，於是股票被標成已更新，實際上悄悄落後。結尾照樣印綠色的 `Updated 1089/1089 stocks`，錯誤只會在下次選股時以「被排除 N 檔」的形式冒出來。

現在**所有請求都失敗時會往外拋**，而不是回傳空表：

- 拋出去才會觸發退避重試（1 秒、2 秒）。限流本來就是暫時的，退避後多半就成功了——實測 428 從 105 次降到 3 次，落後的股票從 73 檔降到 4 檔，而那 4 檔是 TWSE 自己也沒有更新的資料。
- 真的救不回來就變成一次**看得見的更新失敗**，並且寫進結尾那一行：

```
⚠ 3 檔更新失敗（2317、2454、3008）——這些股票的資料仍停在更新前的日期。
  多半是 TWSE 限流（HTTP 428）；再跑一次同樣的指令通常就會補齊。
```

「查無資料」（`stat` 不是 OK，例如已下市）**不算失敗**——那是答案，不是故障，維持回傳空表。

T86（三大法人買賣超日報）現在也遵守同一條界線。它的空表原本有兩種來源、意義相反：**假日**是答案（那天沒有開盤），**428 限流**是故障（資料其實存在）。舊行為把 428 吞掉後回傳空表，兩者長得一模一樣；更新端若據此把那天當成假日記下來，法人買賣超就會永久缺一段。

#### 「已是最新」比的是可得最新交易日，不是今天

更新前要先回答一個問題：現在去抓，有沒有可能拿到比資料庫更新的東西？

這裡的答案曾經是「日曆上的今天」（`latest >= today` 才跳過）。但 `latest` 是最後一個**交易日**，兩者只有在「今天是交易日、而且當日行情已經入庫」時才會相等。其餘時間——週末、假日、每天下午四點以前——1089 檔股票每一檔都會為了確認「沒有新資料」各發一次請求，抓回空表，印一行 `already up to date`，再一起撞上 428 限流。實測 2026-08-21 上午，資料庫已經完整更新到 08-20，這樣的空轉是 **1086 檔**。

而且空轉不便宜：TWSE 的 `STOCK_DAY` 以月為單位查詢，就算只想補一天也是抓整月，跨月時是兩次請求。

現在比的是**可得最新交易日**——以現在的時間推算，來源已經公布到哪一天。三件事決定它：

| 因素 | 規則 |
|------|------|
| 公布時間差 | 當日收盤資料要等盤後彙整，**下午四點**以前問，最新的仍是前一個交易日 |
| 週末 | 沒有交易，往前退到最近的平日 |
| 國定假日 | 沒有內建行事曆可查，改用觀察到的事實（見下） |

假日靠觀察補上：整批更新過後，若**真的問過來源**、**沒有任何一檔抓失敗**、而且**完全沒有抓到新資料**，就把那一天記進 `market_calendar`，之後不必再問第二遍。三個條件缺一不可——全部快取命中時我們根本沒問過，一檔失敗就足以讓「全市場都沒有」這個推論失效。

還有兩道門檻，都是實測踩出來的：

- **只記已經過完的日子**，否則「下午四點剛過、來源還沒公布」會被誤記成假日，當天就再也抓不到當日行情。
- **手上已經有那天的行情就不准記**。上面那個推論是從「問過的那幾檔」得來的，而問得到的往往正是落後或停止交易的那幾檔——那種樣本交不出資料是常態，跟市場有沒有開盤無關。2026-08-21 實測：1089 檔裡 1086 檔快取命中，只有 3 檔落後的被問到、都回空表，於是 08-20 這個 1086 檔都有行情的正常交易日被記成了假日。資料庫自己就是反證，現在由它否決。

若那一天後來真的抓到資料，這筆記錄也會自動撤銷。

一個連假因此最多只被掃一次。`update --type institutional` 也吃同一份行事曆：它的掃描上界改成可得最新交易日，並且不再每跑一次就把區間內每個假日重問一遍。

資料庫已經比推算更新時（來源提前公布、或這台機器的時鐘慢了），以資料庫的**基準日**為準——否則會為了抓比手上還舊的東西再掃一次全市場。

**基準日由多數股票決定，不是全表 `MAX(date)`。** 用最大值很脆弱：只要有 1 檔多抓了一天（來源給了一根未來的 K 棒、或單獨補抓了某檔），其餘 1088 檔就全部變成「落後」，篩選幾乎回傳空的，而訊息只會平靜地說「已排除 1088 檔」。跑在基準日**之前**的被排除，跑在**之後**的照樣參與——它反而是資料最新的那一檔。

不下 `--volume-spike` 時，這些股票照常列出，量比與真實日期都在，不是把資料藏起來。

#### EPS 與營收年增率的資料來源

這兩欄各有自己的來源與頻率，`fundamentals` 表**不含**它們：

| 欄位 | 來源 | 頻率 | 抓取指令 |
|------|------|------|----------|
| `PE Ratio`、`Div Yield` | TWSE `BWIBBU_d` | 每日 | `update --type fundamental` |
| `EPS(累計)` | TWSE 營益分析彙總表 `t187ap14_L` | 每季 | `update --type financials` |
| `營收年增率%` | TWSE 月營收彙總表 `t187ap05_L` | 每月 | `update --type revenue` |

`EPS(累計)` 是**累計至該季**的每股盈餘，不是單季：2026Q2 的數字代表上半年合計。想比較單季表現要自行相減。

> **修正**：`fundamentals` 的 `eps` / `revenue` / `revenue_yoy` / `roe` 四欄從專案建立以來就沒有任何來源會填入——唯一寫入該表的 `BWIBBU_d` 只提供本益比與殖利率。因此 screen 表格的 EPS 與 Revenue YoY 欄永遠顯示 `-`，而 `--eps-growth-min` / `--revenue-yoy-min` **永遠回傳 0 檔且不報錯**，看起來就像「市場上沒有符合的股票」。現已改讀上表的兩張新資料表。**請重跑任何用過這兩個條件的篩選**。

若條件用到的欄位在資料庫裡完全沒有資料，`screen run` 會直接告訴你該執行哪個 `update` 指令並以錯誤碼結束，而不是回傳一份空清單。REST API 對應回 `409`。

ROE 目前沒有免費來源，`roe_analysis` 一律回傳空表——寧可明講沒有，也不要顯示一張全是空值的表格。

### watchlist

Manage named watchlists and screen against them.

```bash
# Create a watchlist with comma-separated stock IDs
tw-stock-analyzer watchlist create semiconductors --stocks 2330,2454,3711

# List all watchlists
tw-stock-analyzer watchlist list

# Delete a watchlist by ID
tw-stock-analyzer watchlist delete 1

# Screen stocks within a watchlist
tw-stock-analyzer watchlist screen 1 --pe-max 20
```

### backtest

Run portfolio backtest simulations with signal-based trading strategies.

```bash
# Run a backtest on a single stock with MA crossover signal
tw-stock-analyzer backtest run --stocks 2330 --capital 1000000

# Run with custom signal method and date range
tw-stock-analyzer backtest run --stocks 2330,2454 --signal rsi --start 2024-01-01 --end 2024-12-31

# Screen all cached stocks first, then backtest the matched universe
tw-stock-analyzer backtest run --pe-max 20 --rsi-max 40 --start 2024-01-01

# Restrict the candidate universe before screening
tw-stock-analyzer backtest run --stocks 2330,2317,2454 --volume-spike 2 --spike-direction up

# Apply criteria saved with `screen run --name ...`
tw-stock-analyzer backtest run --screen-id 3 --start 2024-01-01

# Save the result to the database
tw-stock-analyzer backtest run --stocks 2330 --capital 500000 --save

# List saved backtest results
tw-stock-analyzer backtest list

# Show detailed metrics for a saved backtest
tw-stock-analyzer backtest show 1

# Delete a saved backtest
tw-stock-analyzer backtest delete 1
```

Options for `backtest run`:

| Option              | Description                                           |
|---------------------|-------------------------------------------------------|
| `--stocks, -s`      | Optional comma-separated candidate universe; without screen criteria, defaults to `2330` |
| `--capital, -c`     | Initial capital (default: 1,000,000)                  |
| `--signal`          | Signal method: `ma_cross`, `rsi`, `macd` (default: `ma_cross`) |
| `--start`           | Start date YYYY-MM-DD (default: `2024-01-01`)         |
| `--end`             | End date YYYY-MM-DD (default: today)                  |
| `--save`            | Save the result to the database for later review       |
| `--screen-id`       | Apply a saved screen as the backtest universe filter   |

`backtest run` also accepts every filtering option documented in the
[`screen run` criteria table](#screen): PE, RSI, 20-day average volume,
volume spike and direction, MA crossover and trend alignment, EPS, monthly
revenue growth, and dividend yield. The flow is candidate universe → current
screen → matched stocks → backtest.

> **Backtest bias warning:** screening uses the current database snapshot.
> The database does not preserve complete point-in-time publication
> availability for historical fundamentals, so applying today's screen to a
> historical period can introduce look-ahead and survivorship bias. CLI output
> and REST responses identify this mode as `current_snapshot`.

Available signal methods:

| Method      | Description                                                  |
|-------------|--------------------------------------------------------------|
| `ma_cross`  | Buy when short-term SMA crosses above long-term SMA (20/60)  |
| `rsi`       | Buy when RSI crosses above oversold (30), sell below overbought (70) |
| `macd`      | Buy when MACD line crosses above signal line                 |

Backtest results include:
- **Total Return** — overall portfolio return (%)
- **CAGR** — Compound Annual Growth Rate
- **Volatility** — annualized portfolio volatility
- **Sharpe Ratio** — risk-adjusted return (risk-free rate: 2%)
- **Max Drawdown** — maximum peak-to-trough decline
- **Win Rate** — percentage of positive trading days
- **Equity Curve** — daily portfolio value over the simulation period

### serve

Start the FastAPI REST API server.

```bash
# Start on default port 8000
tw-stock-analyzer serve

# Start with custom port and host
tw-stock-analyzer serve --port 8080 --host 0.0.0.0
```

Once running, visit `http://localhost:8000/docs` for interactive Swagger documentation.

## REST API

The API server exposes all analysis features as JSON endpoints:

| Method | Endpoint                          | Description                    |
|--------|-----------------------------------|--------------------------------|
| GET    | `/health`                         | Health check                   |
| GET    | `/stocks`                         | List all stocks                |
| GET    | `/stocks/{id}/prices`             | OHLCV price data               |
| GET    | `/stocks/{id}/analysis`           | Technical indicators           |
| GET    | `/stocks/{id}/fundamentals`       | Fundamental data               |
| GET    | `/stocks/{id}/institutional`      | Institutional holdings         |
| POST   | `/stocks/{id}/update`             | Trigger data fetch             |
| POST   | `/stocks/update-existing`          | Update all stocks already in the database |
| POST   | `/stocks/{id}/report`             | Generate analysis report       |
| POST   | `/screen`                         | Run or save stock screening criteria |
| GET    | `/screens`                        | List saved screens             |
| GET    | `/screens/{criteria_id}`          | Load and run a saved screen    |
| DELETE | `/screens/{criteria_id}`          | Delete a saved screen          |
| POST   | `/backtest`                       | Run a portfolio backtest, optionally with all screen filters |

Example usage:

```bash
# Get price data
curl http://localhost:8000/stocks/2330/prices?limit=5

# Run technical analysis
curl "http://localhost:8000/stocks/2330/analysis?indicators=ma,rsi,macd"

# Screen stocks
curl -X POST "http://localhost:8000/screen?pe_min=10&pe_max=20&rsi_max=30"

# Run and save a screen, then list or execute saved criteria
curl -X POST "http://localhost:8000/screen?pe_max=20&rsi_max=40&name=value-momentum"
curl "http://localhost:8000/screens"
curl "http://localhost:8000/screens/1"

# 均線交叉：MA5 於最近 5 個交易日內上穿 MA10
curl -X POST "http://localhost:8000/screen?ma_crossover=5x10&ma_within=5"

# 死亡交叉
curl -X POST "http://localhost:8000/screen?ma_crossover=5x10&ma_direction=down&ma_within=5"

# 最新一季 EPS 與月營收年增率
curl -X POST "http://localhost:8000/screen?eps_min=5&revenue_yoy_min=30"

# 爆量：當日量達前 20 日均量的 2 倍以上
curl -X POST "http://localhost:8000/screen?volume_spike=2&volume_min=500000"

# 爆量上漲
curl -X POST "http://localhost:8000/screen?volume_spike=2&volume_min=500000&spike_direction=up"
```

`/screen` 在條件用到的欄位完全沒有資料時回傳 **409**（訊息會指出該跑哪個 `update`），而不是 200 加一份空清單——後者會被呼叫端讀成「沒有符合的股票」。

```bash
# Incrementally update all stocks already present in the database
curl -X POST "http://localhost:8000/stocks/update-existing?data_type=daily"

# Run a screened backtest over all cached stocks
curl -X POST "http://localhost:8000/backtest?pe_max=20&rsi_max=40&capital=1000000&signal=ma_cross&start_date=2024-01-01&end_date=2024-12-31"

# Backtest a saved screen within an explicit candidate universe
curl -X POST "http://localhost:8000/backtest?stocks=2330,2317,2454&screen_id=1&start_date=2024-01-01"
```

## Docker Multi-Service

The project includes 3 services defined in `docker-compose.yml`, all built from the same image:

| Service     | Port | Starts with bare `up`? | Purpose                       |
|-------------|------|------------------------|-------------------------------|
| `api`       | 8000 | yes                    | FastAPI REST server           |
| `streamlit` | 8501 | yes                    | Interactive web dashboard     |
| `cli`       | —    | no (profile `cli`)     | One-shot `update`/`analyze`/… |

`cli` sits behind a compose profile so a bare `docker compose up` does not start it — it would only print `--help` and exit. `docker compose run --rm cli …` targets it explicitly, so the profile never gets in the way.

```bash
# Start the API server in background
docker compose up -d api
curl http://localhost:8000/health          # {"status":"ok"}

# Start the Streamlit dashboard
docker compose up streamlit                # http://localhost:8501

# Run CLI commands
docker compose run --rm cli update --stock 2330 --start 2015-01-01
docker compose run --rm cli replay list
docker compose run --rm cli backtest run --stocks 2330 --save
```

**Volumes and rebuilds.** All services share `./data`, so the SQLite database and logs are the same files the native install uses. `cli` additionally bind-mounts the whole repo (`./:/app`), so it always runs your current working tree. `api` and `streamlit` run the code **baked into the image** — after changing source, rebuild them:

```bash
docker compose build && docker compose up -d --force-recreate streamlit
```

**File ownership.** The entrypoint drops to a non-root `appuser` under real Docker, but detects rootless Docker/Podman (where in-namespace UID 0 already maps to your host user) and stays put — chowning there would remap `./data` to an inaccessible subordinate UID. Either way the database written from a container stays readable by your host user.

## Streamlit Dashboard

Launch the interactive Streamlit dashboard:

```bash
# From project root
streamlit run src/twstock_analyzer/streamlit_app/app.py

# With custom port
streamlit run src/twstock_analyzer/streamlit_app/app.py --server.port 8502

# Or in Docker (port 8501)
docker compose up streamlit
```

Point `streamlit run` at `app.py`, not at the package directory or `__init__.py`: the directory has no `streamlit_app.py` for Streamlit to find, and `__init__.py` only imports `main()` without calling it, which renders a blank page.

### Dashboard Pages

- **總覽 (Overview)** — 股票清單、最新／最早資料日、各股最新行情摘要。
- **個股分析 (Stock Detail)** — 收盤走勢、均線、布林帶，RSI／MACD／KD 分頁，基本面面板（本益比、每股盈餘、營收年增率、殖利率，以及 EPS 與月營收年增率走勢；每個數字都標明自己屬於哪一期，因為三者的頻率不同），以及**三大法人**面板（外資／投信／自營商／合計買賣超，單位為張，附近 30 日明細）。含「更新資料」按鈕。介面為全中文。
- **資料管理 (Data Manager)** — 快取資訊、各資料表筆數、各股資料涵蓋範圍。
- **Replay (回放練習)** — see below.

## Replay — 逐日回放與模擬交易

Pick a stock and a start date, then walk the market forward one trading day at a time, seeing only what was knowable at that moment, and place simulated orders along the way.

**Before the first replay**, backfill the stock you want to practise on (the default cache only holds one year):

```bash
tw-stock-analyzer update --stock 2330 --start 2015-01-01
tw-stock-analyzer update --stock 2330 --type institutional --start 2015-01-01
tw-stock-analyzer update --stock 2330 --type dividend --start 2015-01-01
```

Then open the Streamlit dashboard and choose **🎬 回放練習**. Replay reads local data only — it never calls an API mid-session, so playback stays smooth and every session is reproducible.

**The rules it enforces:**

- The chart, indicators and institutional panel stop at the current day. Nothing later is visible.
- Orders fill at the **next** trading day's open, never today's close — you decided after seeing today's close, so today's close was not available to you (see `docs/adr/0001-fill-at-next-open.md`).
- Brokerage fee (0.1425%, minimum NT$20, discount configurable) and 0.3% transaction tax on sales are charged on every fill.
- An order that the cash cannot cover is voided whole and labelled — never silently shrunk.
- Prices are raw, never adjusted for dividends; ex-dividend days are marked on the chart instead (see `docs/adr/0002-no-adjusted-prices.md`).
- Rewinding to a day discards that day's decisions and everything after it.
- At the end you get total return, win rate, max drawdown, and the **Buy & Hold** comparison — without which you cannot tell skill from a rising tide.

### Reviewing sessions from the terminal

```bash
# List every replay session
tw-stock-analyzer replay list

# Performance summary vs Buy & Hold
tw-stock-analyzer replay show 3

# Export the trade detail for spreadsheet analysis
tw-stock-analyzer replay export 3 --output trades.csv
```

These commands are read-only; they never move a session's cursor.

## 圖表慣例

所有 K 線圖（個股分析、回放練習、匯出的 HTML 報表）共用同一套規則：

- **紅漲綠跌**（台股慣例）。若要改成西方慣例，把 `visualization/charts.py` 裡的 `UP_COLOR` / `DOWN_COLOR` 對調即可，成交量顏色會自動跟著換。
- **成交量柱與 K 棒同色**：逐日依當天漲跌上色，判斷規則與蠟燭相同（收盤 ≥ 開盤為漲）。
- **非交易日不佔位置**：週末、國定假日與停牌日一律從時間軸移除，K 棒之間不會出現空白。判斷依據是「這一天在資料裡不存在」，所以停牌與連假不需要另外處理。

## Project Structure

```
TwStockAnalyzerV2/
├── CONTEXT.md                  # Domain glossary
├── docs/adr/                   # Architecture decision records
├── Dockerfile
├── docker-compose.yml          # Multi-service: api, streamlit (+ cli profile)
├── docker-entrypoint.sh
├── .dockerignore
├── .env.example
├── pyproject.toml
├── README.md
├── data/                       # Local SQLite database & logs
│   ├── twstock.db              # Cached stock data
│   └── twstock.log             # Application log
├── src/
│   └── twstock_analyzer/
│       ├── __init__.py
│       ├── cli/
│       │   └── main.py         # Typer CLI entry point (9 command groups)
│       ├── analysis/
│       │   ├── technical.py    # Technical indicators
│       │   ├── fundamental.py  # Fundamental analysis
│       │   └── institutional.py# Foreign/margin tracking
│       ├── api/
│       │   └── server.py       # FastAPI REST server (10 endpoints)
│       ├── backtesting/
│       │   ├── engine.py       # Portfolio simulation
│       │   ├── signals.py      # Signal generation
│       │   ├── portfolio.py    # Portfolio config
│       │   └── metrics.py      # Performance metrics
│       ├── data/
│       │   ├── loader.py       # Data loading with retry/fallback
│       │   └── sources/
│       │       ├── finmind.py  # FinMind API source (prices, institutional, dividends)
│       │       ├── twse.py     # TWSE source
│       │       └── yahoo.py    # Yahoo Finance source
│       ├── db/
│       │   ├── schema.py       # SQLite schema definitions
│       │   └── repository.py   # Data access layer
│       ├── replay/
│       │   ├── service.py      # Replay Session, Cursor, simulated trading
│       │   └── costs.py        # Brokerage fee + transaction tax
│       ├── screening/
│       │   ├── screener.py     # ScreenCriteria + run_screen()
│       │   ├── financials.py   # 季報／月營收表的「最新一期」查詢與建表
│       │   ├── stock_names.py  # 代號 → 公司簡稱對照表
│       │   └── watchlist.py    # Watchlist CRUD
│       ├── streamlit_app/
│       │   ├── __init__.py     # Re-exports main(); NOT the streamlit target
│       │   ├── app.py          # Dashboard entry point (4 pages)
│       │   └── replay_page.py  # Replay page (thin: no logic, no SQL)
│       ├── utils/
│       │   └── logger.py       # Unified logging
│       └── visualization/
│           ├── charts.py       # Plotly chart generation
│           └── report.py       # Report rendering
└── tests/                      # 253 unit and integration tests
```

## Data Sources

| Source  | Description                              | Config               |
|---------|------------------------------------------|----------------------|
| FinMind | Taiwan financial data via FinMind SDK    | `FINMIND_API_TOKEN`  |
| TWSE    | Taiwan Stock Exchange Corporation API    | None (always available) |

The app uses a fallback chain: FinMind → TWSE. If no FinMind token is configured, it uses TWSE directly.

### Getting a FinMind API Token

1. Register at [FinMind](https://finmind.github.io/)
2. Apply for an API token via their platform
3. Add it to your `.env` file: `FINMIND_API_TOKEN=your_token_here`

## Logging

Logs are written to `data/twstock.log` with rotation (5 MB max, 5 backups; rotated
files are suffixed `.YYYYMMDD-HHMM`).

Log format — the same columns are used for the log file **and** for everything the
CLI prints to the terminal:
```
timestamp         | level | module     | status   | message                | context                   | metrics
2024-01-15 10:30  | INFO  | cli.update | START    | Fetching data...       | stock=2330 source=finmind |
2024-01-15 10:30  | INFO  | cli.update | PROGRESS | Updating 368/1089: 2457|                           |
2024-01-15 10:31  | ERROR | cli.update | FAIL     | Fetch failed for 9999  | stock=9999                | rows=0
```

Two kinds of output are deliberately *not* prefixed:

- **Rich tables** (`list`, `analyze`, `screen`) — a prefix would break the column
  alignment. The rendered table is still written to the log file under `status=TABLE`.
- **Machine-readable payloads** (`report --format json --output -`, `analyze --output
  json`) — stdout stays a valid JSON document so it can be piped into `jq`. The log
  file records a one-line description of the event instead.

Enable verbose/debug logging:
```bash
tw-stock-analyzer -v update --stock 2330
```

## FAQ / Troubleshooting

### No data found for stock

Run `update` first to fetch data:
```bash
tw-stock-analyzer update --stock 2330
```

### FinMind API errors

Ensure `FINMIND_API_TOKEN` is set in `.env`. Check that the token is valid on the FinMind platform.

### "Module not found" errors

Make sure you've installed the package in editable mode:
```bash
pip install -e .
```

### Docker build fails

Ensure Docker is running and you have enough disk space. Try:
```bash
docker compose build --no-cache
```

### `docker compose` fails immediately with an env-file error

Every service declares `env_file: .env`, and compose refuses to start when that file is missing. Create it first:
```bash
cp .env.example .env
```

### Docker multi-service not starting

Ensure the `./data` directory exists (all services mount it):
```bash
mkdir -p data
docker compose up -d api
```

### Streamlit in Docker exits with "File does not exist: …/streamlit_app.py"

You are on an image built before the entrypoint fix. Rebuild:
```bash
docker compose build && docker compose up streamlit
```

### Streamlit dashboard is blank

Two different causes:

1. **You pointed `streamlit run` at `__init__.py`** — it imports `main()` but never calls it. Use `app.py`.
2. **No data cached yet** — fetch some first:
```bash
tw-stock-analyzer update --stock 2330
streamlit run src/twstock_analyzer/streamlit_app/app.py
```

### Code changes do not show up in the Docker dashboard or API

Those two services run the code baked into the image, not your working tree. Rebuild and recreate:
```bash
docker compose build && docker compose up -d --force-recreate streamlit api
```

### 篩選結果的名稱欄都是 `-`

名稱是存在本地的，尚未抓過就沒有：

```bash
tw-stock-analyzer update stocks
```

### 均線交叉篩選結果是空的

四種可能，依序確認：

1. **視窗太窄** — `--ma-within` 預設是 1，意思是「今天才剛穿越」。想找最近一週的，加 `--ma-within 5`。
2. **交叉是事件不是狀態** — 已經站上均線一個月的股票不算穿越，不會被列出來。
3. **趨勢條件把一半篩掉了** — 預設要求交叉當日兩條均線同方向，跌勢中反彈造成的交叉會被排除。加 `--no-ma-trend-align` 可以看到全部的交叉，用它比對就知道少掉的是不是這個原因。
4. **歷史資料不夠** — 判斷 `5x20` 至少需要 21 根 K 棒（開著趨勢條件則需要 23 根）。資料不足的股票會被略過（而不是用不完整的均線硬算）：

```bash
tw-stock-analyzer update --stock 2330 --start 2015-01-01
```

### Stock ID validation failed

Stock IDs must be exactly 4 digits (e.g., `2330` for TSMC, `2454` for MediaTek).

### Backtest returns no data

Ensure the stocks have price data in the selected date range:
```bash
tw-stock-analyzer update --stock 2330
tw-stock-analyzer backtest run --stocks 2330 --start 2024-01-01
```
