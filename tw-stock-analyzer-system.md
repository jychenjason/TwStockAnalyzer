# tw-stock-analyzer-system - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 一個臺股個人研究工具，從命令列下指令就能自動抓取股價/財報/籌碼資料、計算技術指標（K線、MA、MACD、RSI、KD、布林通道）、產出分析圖表。全部資料存本地 SQLite，重複使用不用重抓。還有一個可選的 Streamlit 網頁儀表板。支援 Docker 一鍵啟動。

**Why this approach:** Python + FinMind/TWSE 免費資料源，SQLite 本地快取避免重複 API 呼叫，CLI 為主方便自動化與腳本整合，模組化架構讓 Phase 2 選股回測可以無痛擴充。Fallback 機制確保資料源失效時自動降級。

**What it will NOT do:** 不提供即時報價、不下單交易、沒有多人認證、Phase 2（選股策略＋回測）要等 Phase 1 完成後才實作。

**Effort:** Large — Phase 1, 14 todos across 5 waves
**Risk:** Low — 成熟技術棧，所有 API 皆為官方公開免費
**Decisions to sanity-check:** 資料源優先順序（FinMind→TWSE→error）、快取策略（Cache-Aside）、Log 統一格式、Fallback 鏈路設計

---

> TL;DR (machine): effort=Large, risk=Low, Phase1 deliverables=Data cache layer + Technical/Fundamental/Institutional analysis CLI + Streamlit dashboard + Docker

## Scope
### Must have
- Data acquisition from FinMind (primary) and TWSE OpenAPI (fallback) with automatic failover
- SQLite local cache with Cache-Aside pattern (check → hit → return / miss → fetch → store)
- Cache staleness tracking: each table has `last_updated` timestamp; `--force` flag bypasses cache
- Technical analysis: K-line charts, Moving Averages, MACD, RSI, KD, Bollinger Bands
- Fundamental analysis: EPS, P/E ratio, dividend yield, monthly revenue trend, ROE
- Institutional analysis:三大法人買賣超, margin trading, shareholding distribution
- CLI interface (Typer + Rich) with commands: `update`, `analyze`, `report`, `list`
- Streamlit dashboard (optional, Docker does NOT depend on it)
- Unified logging: timestamp | level | module(source) | status | message | metrics
- Dockerfile + docker-compose.yml for containerized deployment
- Usage documentation in README.md

### Must NOT have (guardrails, anti-slop, scope boundaries)
- NO Phase 2 features (stock screening, backtesting engine) — those are deferred
- NO real-time tick/quote streaming (T+1 daily data only)
- NO order execution / trading API integration
- NO user authentication or multi-user support
  - NO web scraper/crawler implementation (fallback is FinMind→TWSE→error only)
  - NO web server deployment (Streamlit runs locally only)
  - NO Node.js or non-Python dependencies
  - NO external database servers (SQLite only, no PostgreSQL/MySQL)
  - NO overwriting or modifying files outside src/ and data/ directories
  - NO committing API keys or tokens to version control

## Verification strategy
> All unit/integration tests are agent-executed with mocked sources. F3 (end-to-end smoke test) is the only step requiring a real API token.
- Test decision: tests-after + pytest framework. API-dependent tests use mocks (unittest.mock, responses library).
- Evidence: .omo/evidence/task-<N>-tw-stock-analyzer-system.<ext>
- Python venv: /home/jason/projects/venv312/ (use `source /home/jason/projects/venv312/bin/activate` before any python/pip command)
- All test commands must be run from project root `/home/jason/projects/TwStockAnalyzer`

## Execution strategy
### Parallel execution waves
- Wave 1: Foundation (3 todos, fully parallel)
- Wave 2: Data Layer (3 todos, depends on W1)
- Wave 3: Analysis Engine (3 todos, depends on W2)
- Wave 4: CLI + Visualization + Docker (4 todos, depends on W3)
- Wave 5: Final QA & Documentation (1 todo + final verification wave)

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. Project skeleton | — | W2, W3, W4 | 2, 3 |
| 2. Logging module | — | W2, W3, W4 | 1, 3 |
| 3. SQLite schema | — | W2, W3, W4 | 1, 2 |
| 4. Data source interface | 1, 2, 3 | 5, 6 | — |
| 5. FinMind source | 4 | — | — |
| 6. TWSE source + fallback | 4 | 7, 8, 9 | — (does NOT need 5 done first) |
| 7. Technical analysis | 6 | 10 | 8, 9 |
| 8. Fundamental analysis | 6 | 10 | 7, 9 |
| 9. Institutional analysis | 6 | 10 | 7, 8 |
| 10. CLI commands | 7, 8, 9 | 11, 13 | — |
| 11. Charting module | 7, 8, 9 | 12, 13 | 10 |
| 12. Streamlit dashboard | 10, 11 | — | — (optional, doesn't block anything) |
| 13. Docker + docs | 10, 11 | 14 | — (does NOT need 12) |
| 14. Integration tests | 13 | FVW | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 1: Foundation

- [x] 1. `pyproject.toml` + project skeleton + venv setup
  What to do / Must NOT do: Create the Python project with src layout. Set up pyproject.toml with project metadata, dependencies with pinned versions:
  - typer>=0.12, rich>=13, pandas>=2.0, pandas-ta>=0.3.16b0 (pin to avoid breaking changes), plotly>=5.18, streamlit>=1.28, requests>=2.31, python-dotenv>=1.0
  - Dev: pytest>=8, pytest-cov>=4, pytest-mock>=3, responses>=0.25 (for mocking HTTP requests)
  - FinMind>=2.0.4 (latest stable as of 2026-07)
  Create directory structure: src/twstock_analyzer/{cli,data,sources,db,analysis,visualization,streamlit_app,utils}/ + tests/ + data/. Use the venv at /home/jason/projects/venv312/. Must NOT commit API keys or .env.
  Parallelization: Wave 1 | Blocked by: — | Blocks: W2, W3, W4
  References: /home/jason/projects/TwStockAnalyzer (empty project root), venv at /home/jason/projects/venv312/
  Acceptance criteria (agent-executable): `cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -c "import typer; import rich; import pandas; import pandas_ta; import plotly; import streamlit; import FinMind; print('OK')"` exits 0
  QA scenarios:
  - Happy: Run the import check above → prints "OK"
  - Failure: Remove a dep → import fails with ModuleNotFoundError
  Evidence: .omo/evidence/task-1-tw-stock-analyzer-system.txt
  Commit: N (no meaningful code yet)

- [x] 2. Logging module (`src/twstock_analyzer/utils/logger.py`)
  What to do / Must NOT do: Implement the unified logging module. Log format: `%(asctime)s | %(levelname)-5s | %(module)-15s | %(context)-30s | %(status)-8s | %(message)s | %(metrics)s`. Timestamp precision: milliseconds (e.g., `2026-07-01 14:30:00.123`). Console handler at INFO level (stdout), RotatingFileHandler at DEBUG level (10MB×5 backups, file: data/twstock.log). Provide convenience functions: `log_fetch(logger, stock_id, source, status, message, **metrics)`, `log_cache(...)`, `log_analysis(...)`, `log_error(...)`. Each auto-fills context and status fields. Auto-create data/ directory if it doesn't exist. Use path relative to CWD (so it works both natively and in Docker). Must NOT use external logging libraries. Must NOT log API keys or tokens. Support context propagation via logging.LoggerAdapter. 
  
  Rotation test: Use `logging.handlers.RotatingFileHandler(maxBytes=1024, backupCount=2)` for testing (smaller size), not the full 10MB.
  
  Parallelization: Wave 1 | Blocked by: — | Blocks: W2, W3, W4
  References: Python logging docs, RotatingFileHandler
  Acceptance criteria (agent-executable):
  ```bash
  cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -c "
  import os, re, logging
  from src.twstock_analyzer.utils.logger import get_logger, log_fetch
  test_log = 'data/test_twstock.log'
  logger = get_logger('test', log_file=test_log, debug=True)
  log_fetch(logger, stock_id='2330', source='finmind', status='SUCCESS', message='Test fetch', rows=250, dur='0.78s')
  assert os.path.exists(test_log), 'log file not created'
  with open(test_log) as f:
      line = f.read().strip()
  # Check format: timestamp | INFO | test | stock=2330 source=finmind | SUCCESS | Test fetch | rows=250 dur=0.78s
  assert re.search(r'\| INFO  \| test', line), f'bad format: {line}'
  assert 'SUCCESS' in line
  assert 'stock=2330' in line
  print('PASS')
  os.remove(test_log)
  "
  ```
  QA scenarios:
  - Happy: Log fetch success → line matches regex with all fields present
  - Happy: data/ directory auto-created if missing
  - Happy: Rotation works → force rotation by sending >1KB of logs, verify backup file exists
  - Failure: Cannot create log file (permission denied) → logger falls back to console only, raises warning (not crash)
  Evidence: .omo/evidence/task-2-tw-stock-analyzer-system.txt
  Commit: Y | feat(logger): add unified logging module

- [x] 3. SQLite database schema + repository (`src/twstock_analyzer/db/`)
  What to do / Must NOT do: Create schema.py with table definitions. IMPORTANT: technical indicators are computed on-the-fly and NOT stored in DB (Phase 1). Tables:
  - `daily_prices` (stock_id TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adj_close REAL, fetched_at TEXT, PRIMARY KEY (stock_id, date))
  - `fundamentals` (stock_id TEXT, report_date TEXT, period TEXT, eps REAL, pe_ratio REAL, dividend_yield REAL, revenue REAL, revenue_yoy REAL, roe REAL, fetched_at TEXT, PRIMARY KEY (stock_id, report_date))
  - `institutional_trading` (stock_id TEXT, date TEXT, foreign_buy REAL, foreign_sell REAL, foreign_net REAL, margin_balance REAL, margin_usage_ratio REAL, short_balance REAL, fetched_at TEXT, PRIMARY KEY (stock_id, date))
  - `cache_metadata` (cache_key TEXT PRIMARY KEY, stock_id TEXT, data_type TEXT, source TEXT, fetched_at TEXT, date_from TEXT, date_to TEXT, row_count INT)
  
  Add indexes:
  - `CREATE INDEX idx_daily_stock_date ON daily_prices(stock_id, date);`
  - `CREATE INDEX idx_fund_stock_date ON fundamentals(stock_id, report_date);`
  - `CREATE INDEX idx_inst_stock_date ON institutional_trading(stock_id, date);`
  
  Create repository.py with CRUD plus:
  - `get_latest_date(table, stock_id)` → max(date) string
  - `has_data(table, stock_id, date_from, date_to)` → returns coverage dict {has_data: bool, date_min, date_max, row_count}
  - `upsert(table, df)` — bulk INSERT OR REPLACE using `pd.to_sql(if_exists='replace', method='multi')` or `executemany()` for non-pandas inputs. NOT row-by-row insert.
  - `get_cache_metadata(stock_id, data_type)` → cache metadata dict
  - Enable WAL mode on connection for concurrent reads+writes: `PRAGMA journal_mode=WAL;`
  - Use context manager for connection auto-close
  - Schema versioning: store schema_version=1 in a `_meta` table for future migrations
  
  Data file: data/twstock.db (auto-created in data/ directory). Must NOT use ORM (raw sqlite3 is fine). Must NOT hardcode paths. Must NOT create a `technical_indicators` table (computed on-the-fly in Todo 7). Must NOT add industry/sector tables.
  
  Parallelization: Wave 1 | Blocked by: — | Blocks: W2, W3, W4
  References: SQLite Python docs, WAL mode docs, pd.to_sql docs
  Acceptance criteria (agent-executable): 
  ```bash
  cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -c "
  import sqlite3, os
  db_path = '/tmp/test_twstock.db'
  if os.path.exists(db_path): os.remove(db_path)
  from src.twstock_analyzer.db.schema import create_tables
  from src.twstock_analyzer.db.repository import upsert, has_data
  create_tables(db_path)
  conn = sqlite3.connect(db_path)
  assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
  # Insert test data
  import pandas as pd
  df = pd.DataFrame({'stock_id': ['2330'], 'date': ['2024-01-02'], 'open': [500.0], 'high': [510.0], 'low': [499.0], 'close': [505.0], 'volume': [10000000], 'adj_close': [505.0], 'fetched_at': ['2024-01-02T14:30:00']})
  upsert('daily_prices', df)
  coverage = has_data('daily_prices', '2330', '2024-01-01', '2024-01-31')
  assert coverage['has_data'] == True
  assert coverage['row_count'] == 1
  print('PASS')
  "
  ```
  QA scenarios:
  - Happy: Insert + query roundtrip → all fields match, coverage dict correct
  - Happy: WAL mode → PRAGMA returns 'wal'
  - Happy: Indexes exist → `PRAGMA index_list('daily_prices')` returns non-empty
  - Failure: Insert duplicate PK → REPLACE succeeds (no IntegrityError)
  - Failure: upsert with wrong columns → raises ValueError with column mismatch message
  Evidence: .omo/evidence/task-3-tw-stock-analyzer-system.txt
  Commit: Y | feat(db): add SQLite schema and repository

### Wave 2: Data Layer

- [x] 4. Data source abstraction (`src/twstock_analyzer/data/`)
  What to do / Must NOT do: Create abstract base class `BaseDataSource` with interface: `fetch_daily(stock_id, start_date, end_date) -> DataFrame`, `fetch_fundamentals(stock_id, period) -> DataFrame`, `fetch_institutional(stock_id, date) -> DataFrame`, `name -> str` property. All methods raise `DataFetchError` on failure. Define `DataFetchError` and `DataFallbackError` exception classes. Must NOT contain any source-specific logic.
  Parallelization: Wave 2 | Blocked by: 1, 2, 3 | Blocks: 5, 6
  References: Abstract Base Classes (abc) in Python
  Acceptance criteria (agent-executable): Create a mock subclass, instantiate it, call each method with test args → returns DataFrame or raises NotImplementedError.
  QA scenarios:
  - Happy: Mock source returns expected DataFrame
  - Failure: Method not implemented → raises NotImplementedError
  Evidence: .omo/evidence/task-4-tw-stock-analyzer-system.txt
  Commit: Y | feat(data): add abstract data source interface

- [x] 5. FinMind data source (`src/twstock_analyzer/data/sources/finmind.py`)
  What to do / Must NOT do: Implement `FinMindSource(BaseDataSource)`. Use `FinMind` Python SDK (`from FinMind.data import DataLoader`, pinned to >=2.0.4). Need user to register at finmindtrade.com for API token. Token loaded from `.env` file via python-dotenv (not hardcoded). Implement: `fetch_daily()` → calls `dl.taiwan_stock_daily()`, `fetch_fundamentals()` → calls `dl.finmind_fundamental()` (verify exact free-tier method name), `fetch_institutional()` → calls `dl.finmind_institutional_investors()`. Note: verify all three datasets are available on free tier — if a dataset requires paid plan, the source should raise DataFetchError with clear message (fallback chain will handle). Handle API rate limits (600 req/hr). Apply retry logic from BaseDataSource (mixin/decorator defined in Todo 4). Must use logger from utils.logger with context={"source":"finmind"}. Must NOT hardcode token. Must NOT assume all FinMind SDK methods work in Docker — pin version and test.
  
  **IMPORTANT**: Implementation must include a `should_skip(env_check)` classmethod for the DataLoader to detect missing credentials without crashing.
  
  **Testing strategy (split into two parts):**
  - Part A (unit tests with mocked FinMind): tests verify DataFrame structure, error handling, retry logic using `unittest.mock.patch()` — no real credentials needed
  - Part B (integration test with real token): documented as manual step requiring `.env` 
  
  Parallelization: Wave 2 | Blocked by: 4 | Blocks: — (DataLoader can work with just TWSE; FinMindSource is additive)
  References: https://finmind.github.io/, https://pypi.org/project/finmind/
  Acceptance criteria (agent-executable, Part A only):
  ```bash
  cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -c "
  from unittest.mock import patch, MagicMock
  from src.twstock_analyzer.data.sources.finmind import FinMindSource
  import pandas as pd
  source = FinMindSource()
  # Test 1: Fetch returns correct DataFrame shape
  mock_dl = MagicMock()
  mock_dl.taiwan_stock_daily.return_value = pd.DataFrame({
      'date': ['2024-01-02'], 'open': [500.0], 'high': [510.0],
      'low': [499.0], 'close': [505.0], 'volume': [10000000]
  })
  with patch.object(source, 'dl', mock_dl):
      df = source.fetch_daily('2330', '2024-01-01', '2024-01-31')
      assert list(df.columns) == ['date', 'open', 'high', 'low', 'close', 'volume']
  # Test 2: Invalid token raises DataFetchError
  from src.twstock_analyzer.data.loader import DataFetchError, DataFallbackError
  assert issubclass(DataFallbackError, DataFetchError)  # fallback is a subclass of fetch error
  print('PASS')
  "
  ```
  QA scenarios:
  - Happy (mock): Mock FinMind returns valid DF → source returns DF with correct columns
  - Failure (mock): Mock raises Exception → source raises DataFetchError after retry exhaustion
  - Failure (mock): Empty token → source.should_skip() returns True
  - Manual: Real token + real API → integration test output saved
  Evidence: .omo/evidence/task-5-tw-stock-analyzer-system.txt
  Commit: Y | feat(data): add FinMind data source

- [x] 6. TWSE OpenAPI source + Fallback chain + Stock ID validation (`src/twstock_analyzer/data/sources/twse.py` + `src/twstock_analyzer/data/loader.py`)
  What to do / Must NOT do: 
  
  **TWSESource**: Implement `TWSESource(BaseDataSource)` using requests to TWSE OpenAPI endpoints. Endpoints: STOCK_DAY_ALL (daily), BWIBBU_d (PE/dividend), opendata/t187ap28_L (institutional). Apply retry logic via BaseDataSource mixin (consistent with Todo 5). Check if TWSE OpenAPI requires auth token (try public endpoints first; if 401, document and implement token from .env). Do NOT implement a web scraper — fallback chain is FinMind→TWSE→error only.
  
  **BaseDataSource retry mixin**: Add retry decorator/mixin to `BaseDataSource` (Todo 4) — retries 3 times with exponential backoff (1s, 2s, 4s). Both FinMindSource and TWSESource use the same retry logic. This must be defined before both Todo 5 and Todo 6; update Todo 4 to include retry support.
  
  **DataLoader**: Implement `DataLoader` class that maintains a list of source *factories/instances* in priority order [FinMind, TWSE]. Each source has a `should_skip()` method to indicate missing credentials. `get_data()` method: iterate available sources, log each attempt (START/SUCCESS/FAILED/SWITCH), on success return data, on all fail raise DataFallbackError. After fetch, store results via repository.upsert() and update cache_metadata. Must use logger from utils.logger. Must NOT expose source iteration logic to callers. DataLoader must work even if FinMindSource has missing credentials (it will be skipped gracefully).
  
  **Stock ID validation**: Add `validate_stock_id(stock_id)` → check 4-digit numeric pattern, raise ValueError with message "Invalid stock ID format: expected 4 digits" for invalid input. Apply before any source call or DB operation. Add `validate_date(date_str)` → parse and format to YYYY-MM-DD, raise ValueError for invalid dates.
  
  **Date normalization**: All dates normalized to YYYY-MM-DD format at the loader level before passing to sources or storage. Also normalize dates returned from sources (some may return YYYYMMDD or other formats).
  
  Parallelization: Wave 2 | Blocked by: 4 | Blocks: 7, 8, 9 (does NOT need Todo 5 — DataLoader works with any subset of registered sources)
  
  References: https://openapi.twse.com.tw/v1/swagger.json, TWSE API docs
  Acceptance criteria (agent-executable):
  ```bash
  cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -c "
  from src.twstock_analyzer.data.loader import DataLoader, validate_stock_id, validate_date
  from src.twstock_analyzer.data.sources.twse import TWSESource
  import pandas as pd
  # Test validation
  validate_stock_id('2330')
  try:
      validate_stock_id('abc')
      assert False, 'should have raised'
  except ValueError as e:
      assert '4-digit' in str(e)
  # Test date normalization
  assert validate_date('2024-01-01') == '20240101'  # API format
  # Test DataLoader with only TWSE source
  loader = DataLoader(sources=[TWSESource()])  # FinMind source not needed
  # Mock HTTP to avoid real call
  from unittest.mock import patch
  with patch('requests.get') as mock_get:
      mock_get.return_value.status_code = 200
      mock_get.return_value.json.return_value = {'data': [['113/01/02','1000','500000','500','510','490','505','+5','100']]}
      result = loader.get_data('2330', 'daily')
      assert isinstance(result, pd.DataFrame)
  print('PASS')
  "
  ```
  QA scenarios:
  - Happy: Mock TWSE returns data → DataLoader returns DataFrame with correct structure
  - Validation: stock_id='abc' → ValueError before any HTTP call
  - Validation: invalid date '2024-13-01' → ValueError
  - Partial source: Only TWSE available (FinMindSource.should_skip()=True) → DataLoader uses TWSE, logs "finmind: skipped (no credentials)"
  - All fail: All sources fail → DataFallbackError with failed_sources list
  - Date norm: Source returns ROC date "113/01/02" → normalized to "2024-01-02"
  Evidence: .omo/evidence/task-6-tw-stock-analyzer-system.txt
  Commit: Y | feat(data): add TWSE source and fallback chain

### Wave 3: Analysis Engine

- [x] 7. Technical analysis module (`src/twstock_analyzer/analysis/technical.py`)
  What to do / Must NOT do: Implement technical indicators using pandas-ta (pinned to >=0.3.16b0). Must support: K-line (OHLC input → candlestick-ready DataFrame), SMA/EMA (5, 10, 20, 60, 120, 240 days), MACD (12, 26, 9), RSI (14), KD/Stochastic (5, 3, 3 — verify pandas-ta uses stoch() with these params), Bollinger Bands (20, 2). Each indicator is a separate method returning DataFrame with appropriate columns. Configurable parameters via kwargs with sensible defaults. Must NOT require TA-Lib (use pandas-ta only). Must validate input has required columns. Must log computation metrics (which indicators, duration). Must NOT store computed indicators in DB (computed on-the-fly from daily_prices).
  Parallelization: Wave 3 | Blocked by: 6 | Blocks: 10 | Can parallelize with: 8, 9
  References: pandas-ta docs, technical indicator formulas
  Acceptance criteria (agent-executable): Load sample price data for 2330 (1 year), call each indicator method → verify output has correct columns. Run `calculate_all()` → returns DataFrame with all indicator columns. Verify early rows have NaN for lookback periods (e.g., first 19 rows of 20-day BB have NaN).
  QA scenarios:
  - Happy: Calculate MACD → output has columns DATE, MACD_12_26_9, MACDs_12_26_9, MACDh_12_26_9
  - Happy: Bollinger Bands → output has columns BBU_20_2, BBM_20_2, BBL_20_2, BBB_20_2
  - Edge: 5 data rows for 20-day MA → returns NaN for MA, no crash
  - Edge: Missing columns (no 'close') → raises ValueError with "required column: close"
  Evidence: .omo/evidence/task-7-tw-stock-analyzer-system.txt
  Commit: Y | feat(analysis): add technical analysis module

- [x] 8. Fundamental analysis module (`src/twstock_analyzer/analysis/fundamental.py`)
  What to do / Must NOT do: Fetch and compute fundamental metrics from DB (pre-populated by data layer). Implement with exact algorithms:
  - `pe_ratio(stock_id, date)` → `close_price / eps` (trailing 12-month EPS). If EPS is 0 or negative, return `None` (not infinite). If no data, return `None`. Log warning for negative EPS.
  - `dividend_yield(stock_id, year)` → `(total_dividend_per_share / avg_close_price) * 100`. Return float or None if no dividend data.
  - `monthly_revenue(stock_id, months=12)` → DataFrame with columns: date, revenue, yoy_growth (percentage). yoy_growth = `(current_month_revenue / same_month_last_year_revenue - 1) * 100`. Handle cases with < 12 months (partial data ok, fewer rows returned, no crash).
  - `eps_trend(stock_id, quarters=8)` → DataFrame with columns: quarter, eps. Compute "trend_direction" as: compare most recent quarter EPS to same quarter prior year → 'up', 'down', or 'flat' (±5% threshold). Return None for insufficient data.
  - `roe_analysis(stock_id, quarters=8)` → DataFrame with columns: quarter, roe. Compute average ROE over period.
  
  Must NOT modify raw data. Must NOT crash on division by zero or missing data. Must log computation duration.
  Parallelization: Wave 3 | Blocked by: 6 | Blocks: 10 | Can parallelize with: 7, 9
  References: FinMind datasets for fundamental data
  Acceptance criteria (agent-executable):
  ```bash
  cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -c "
  from src.twstock_analyzer.analysis.fundamental import FundamentalAnalyzer
  from src.twstock_analyzer.db.repository import upsert
  import pandas as pd
  # Inject test data
  upsert('fundamentals', pd.DataFrame({
      'stock_id': ['2330', '2330'], 'report_date': ['2024-03-31', '2024-06-30'],
      'period': ['2024Q1', '2024Q2'], 'eps': [8.08, 8.62],
      'pe_ratio': [15.2, 14.8], 'revenue': [500000000000, 520000000000],
      'revenue_yoy': [20.4, 22.1], 'roe': [8.08, 8.62]
  }))
  fa = FundamentalAnalyzer()
  result = fa.eps_trend('2330', quarters=4)
  assert isinstance(result, pd.DataFrame), 'must return DataFrame'
  # Test edge: negative EPS
  result2 = fa.pe_ratio('2330', '2024-01-01')
  import numpy as np
  assert result2 is not None  # positive EPS → should have value
  print('PASS')
  "
  ```
  QA scenarios:
  - Happy: Revenue YoY with 12+ months → correct percentage values
  - Edge: Stock with no fundamental data → empty DataFrame returned (no crash)
  - Edge: Negative EPS → pe_ratio returns None, log warning "Negative EPS for 2330"
  - Edge: < 12 months revenue data → returns partial DataFrame (fewer rows, no crash)
  - Edge: EPS trend with < 2 quarters → trend_direction returns 'insufficient_data'
  Evidence: .omo/evidence/task-8-tw-stock-analyzer-system.txt
  Commit: Y | feat(analysis): add fundamental analysis module

- [x] 9. Institutional analysis module (`src/twstock_analyzer/analysis/institutional.py`)
  What to do / Must NOT do: Fetch and compute institutional metrics. Implement: `foreign_investors(stock_id, days=30)` → buy/sell/net DataFrame, `margin_trading(stock_id, days=30)` → margin balance, usage ratio, `shareholding_distribution(stock_id, date)` → holding levels. Compute derived: net buy/sell trend, margin usage change rate. Data sourced from DB. Must NOT modify raw data. Must log computation duration.
  Parallelization: Wave 3 | Blocked by: 6 | Blocks: 10 | Can parallelize with: 7, 8
  References: FinMind institutional investors dataset
  Acceptance criteria (agent-executable): Query a stock → verify foreign_investors returns DataFrame with foreign_buy, foreign_sell, foreign_net columns. Verify margin_trading returns balance and rate columns.
  QA scenarios:
  - Happy: Foreign net buy calculation → correct net = buy - sell
  - Edge: No institutional data for a given date → empty row with 0 values, not NaN
  Evidence: .omo/evidence/task-9-tw-stock-analyzer-system.txt
  Commit: Y | feat(analysis): add institutional analysis module

### Wave 4: CLI + Visualization + Docker

- [x] 10. CLI commands (`src/twstock_analyzer/cli/main.py`)
  What to do / Must NOT do: Implement Typer CLI app with commands:
  - `update [--stock STOCK] [--all] [--type daily|fundamental|institutional] [--force]` — fetch and cache data. `--force` bypasses cache and re-fetches from source.
  - `analyze STOCK [--indicators IND] [--start DATE] [--end DATE] [--output FORMAT]` — run analysis and print Rich table. FORMAT=json prints JSON to stdout. FORMAT=html generates plotly chart.
  - `report STOCK [--format html|png|json] [--output FILE]` — generate full report.
  - `list [--top N]` — list top N stocks with basic info. No --industry filter (industry data not in Phase 1 scope).
  
  Apply stock ID validation (4-digit numeric check) on all STOCK arguments before any DB/API call. Use Rich for colored tables. Use logger for all actions. Must NOT require Streamlit to be running. Must handle KeyboardInterrupt gracefully. Must NOT create any files outside the project directory.
  Parallelization: Wave 4 | Blocked by: 7, 8, 9 | Blocks: 11 | Can parallelize with: 11 (partially)
  References: Typer docs, Rich docs, CLI design from interview
  Acceptance criteria (agent-executable): Run `python -m twstock_analyzer.cli.main --help` → shows all commands. Run `analyze 2330 --indicators ma,rsi --start 2024-01-01 --end 2024-03-31` → prints colored table with data. Run with stock="abc" → error message.
  QA scenarios:
  - Happy: analyze 2330 → Rich table with prices + MA + RSI columns
  - Happy: update --stock 2330 --type daily → log shows SUCCESS fetch
  - Happy: update --stock 2330 --type daily --force → log shows MISS (forced), fetches new data
  - Error: Invalid stock ID "abc" → "Error: Invalid stock ID format" exit code 1
  - Error: Stock not in DB, no network → "Error: No data available" exit code 1
  - Edge: --output json → valid JSON array to stdout
  Evidence: .omo/evidence/task-10-tw-stock-analyzer-system.txt
  Commit: Y | feat(cli): add CLI commands update/analyze/report/list

- [x] 11. Charting / report module (`src/twstock_analyzer/visualization/`)
  What to do / Must NOT do: Implement chart generation using plotly. `plot_kline(df, indicators=None)` → candlestick chart with optional overlay indicators. `plot_technical(df, indicator_list)` → multi-panel subplots (price + volume + RSI + MACD + KD). `generate_report(stock_id, output_path, format="html")` → full analysis report as HTML. Support exporting to HTML (interactive) and PNG (static). Charts must have proper Chinese labels. Must NOT require GUI display server (save to file only). Must NOT use matplotlib (use plotly only for interactivity).
  Parallelization: Wave 4 | Blocked by: 7, 8, 9 | Blocks: 12 | Can parallelize with: 10
  References: Plotly docs, plotly candlestick charts
  Acceptance criteria (agent-executable): Generate a K-line chart for 2330 with MA(20) overlay → saved as HTML file. Open file check: contains `<html>` and `<script>` tags. Generate report as HTML → file exists and >10KB.
  QA scenarios:
  - Happy: K-line chart → HTML with interactive candlestick plot
  - Happy: Report generation → complete HTML with price + indicators + fundamentals sections
  - Edge: No data → graceful empty chart with message, no crash
  Evidence: .omo/evidence/task-11-tw-stock-analyzer-system.txt
  Commit: Y | feat(viz): add plotly charting and report module

- [x] 12. Streamlit dashboard (OPTIONAL — does NOT block any other todo)
  What to do / Must NOT do: Create Streamlit app at `src/twstock_analyzer/streamlit_app/app.py`. Pages: Dashboard (overview of watched stocks from DB), Stock Detail (price chart + indicators + fundamentals + institutional tabs — reuse analysis modules via import), Data Manager (view cache status from cache_metadata table, trigger update). 
  
  Streamlit run command: `streamlit run src/twstock_analyzer/streamlit_app/app.py`. Must NOT require CLI to be the entry point (direct python import of analysis modules is fine). Must handle the case when no data is cached yet (show guidance message). Must NOT be the primary interface (CLI is primary). Must NOT block Docker (Todo 13) or any other todo.
  
  Testing: Static code analysis only — verify app.py imports from twstock_analyzer.analysis.* and twstock_analyzer.db.* (no live server test needed). Document `streamlit run` command in README.
  
  Parallelization: Wave 4 | Blocked by: 10, 11 | Blocks: — (OPTIONAL, blocks nothing)
  References: Streamlit docs
  Acceptance criteria (agent-executable):
  ```bash
  cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -c "
  import ast, sys
  with open('src/twstock_analyzer/streamlit_app/app.py') as f:
      tree = ast.parse(f.read())
  imports = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)}
  from_imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
  assert 'twstock_analyzer.analysis' in str(from_imports) or 'twstock_analyzer.analysis.technical' in str(from_imports), 'must import analysis modules'
  assert 'twstock_analyzer.db' in str(from_imports) or 'twstock_analyzer.db.repository' in str(from_imports), 'must import db modules'
  print('PASS')
  "
  ```
  QA scenarios:
  - Happy: app.py imports from twstock_analyzer.analysis → static analysis passes
  - Happy: app.py imports from twstock_analyzer.db → static analysis passes
  - Edge: No data cached → Dashboard shows "資料尚未載入。請先執行: python -m twstock_analyzer.cli.main update --all" in the UI
  - **Manual**: Optional live test — `streamlit run src/twstock_analyzer/streamlit_app/app.py --server.headless true` → verify browser shows Stock Detail with chart
  Evidence: .omo/evidence/task-12-tw-stock-analyzer-system.txt
  Commit: Y | feat(streamlit): add Streamlit dashboard

- [x] 13. Docker + usage documentation
  What to do / Must NOT do: 
  
  **Dockerfile**: Single-stage build using `python:3.12-slim` base. Steps:
  - Set `WORKDIR /app`
  - Copy requirements.txt, run `pip install --no-cache-dir -r requirements.txt` (keep build cache small)
  - Create non-root user `appuser`, `chown -R appuser:appuser /app/data`, switch to USER appuser
  - ENTRYPOINT ["python", "-m", "twstock_analyzer.cli.main"]
  - Install CJK fonts only if needed for PNG export (document as optional; Plotly HTML output works without fonts)
  
  **docker-compose.yml**: 
  - Service name: app, build: .
  - Use `env_file: .env` (NOT volume mount for .env — docker-compose has built-in env_file support)
  - Volume: `./data:/app/data` (named or bind mount for SQLite persistence)
  - Ensure data/ directory exists on host with write permissions for non-root user
  
  **.dockerignore**: Exclude .git, __pycache__, .omo/, tests/, .env (prevent accidental inclusion)
  
  **README.md** sections: Project Overview | Installation (pip + Docker) | Configuration (.env + FinMind API registration URL) | CLI Commands (update/analyze/report/list with examples) | Streamlit Dashboard (note: `streamlit run src/twstock_analyzer/streamlit_app/app.py` separately) | Project Structure | Data Sources | Logging | FAQ/Troubleshooting
  
  **.gitignore**: Must include .env, __pycache__/, *.pyc, data/twstock.db, .omo/evidence/, .omo/run-continuation/
  
  Must NOT include API tokens in Dockerfile or docs. Must NOT use multi-stage build (single stage is simpler for pure-Python projects). Must NOT expose ports (CLI-only, no web server).
  
  Parallelization: Wave 4 | Blocked by: 10, 11 | Blocks: 14 (does NOT depend on Todo 12 — Streamlit is optional)
  References: Docker best practices, docker-compose env_file docs
  Acceptance criteria (agent-executable): 
  ```bash
  cd /home/jason/projects/TwStockAnalyzer
  # Test build
  docker build -t twstock-analyzer . && echo "BUILD OK"
  # Test .dockerignore exists and excludes .env
  test -f .dockerignore && echo "DOCKERIGNORE OK"
  # Test README has required sections
  grep -q "## CLI Commands" README.md && echo "README OK"
  # Test docker-compose.yml uses env_file (not volume mount for .env)
  grep -q "env_file:" docker-compose.yml && echo "COMPOSE OK"
  ```
  QA scenarios:
  - Happy: Docker build → exits 0, image tagged twstock-analyzer
  - Happy: .dockerignore excludes .env → `docker build` doesn't include .env in build context (verify with `docker history --no-trunc`)
  - Failure: Missing .env file → `docker compose run --rm app analyze 2330` exits with error message "FinMind API token not found. See README.md for setup"
  - Edge: `.env` has 600 permissions → container reads it successfully
  Evidence: .omo/evidence/task-13-tw-stock-analyzer-system.txt
  Commit: Y | feat(docker): add Docker support and usage docs

### Wave 5: Final

- [x] 14. Integration tests + end-to-end verification
  What to do / Must NOT do: 
  **conftest.py**: Create `tests/conftest.py` with shared fixtures:
  - `in_memory_db`: Creates SQLite database in memory with full schema for test isolation
  - `mock_finmind_source`: Returns pre-built DataFrame without real API calls
  - `mock_twse_source`: Returns pre-built DataFrame without real HTTP calls
  - `sample_daily_prices`: DataFrame with 1 year of 2330 daily data for analysis tests
  
  **Test files**:
  - `tests/test_cli.py`: CLI invocation via `typer.testing.CliRunner`, verify correct exit codes and output
  - `tests/test_logger.py`: Log format verification (regex), rotation test (small maxBytes=1024), data/ dir auto-creation
  - `tests/test_db.py`: Schema creation, upsert roundtrip, has_data() coverage, WAL mode, index existence, duplicate PK handling
  - `tests/test_data_sources.py`: Mock all sources, verify BaseDataSource interface compliance, error propagation, retry logic
  - `tests/test_fallback.py`: Mock DataLoader with various source combinations, verify fallback chain ordering and logging
  - `tests/test_analysis.py`: Test technical/fundamental/institutional analyzers with sample data, verify edge cases (NaN, empty, missing columns)
  - `tests/test_integration.py`: Full pipeline test (mock fetch → cache → analyze → report) using mocked sources
  
  Tests must use pytest. Coverage target >70% on core modules (data/, db/, analysis/). Tests must NOT call real APIs. Must use in-memory SQLite. Must NOT modify production database (data/twstock.db).
  
  Parallelization: Wave 5 | Blocked by: 13 | Blocks: FVW
  References: pytest docs, pytest-mock, typer.testing docs
  Acceptance criteria (agent-executable): 
  ```bash
  cd /home/jason/projects/TwStockAnalyzer && source /home/jason/projects/venv312/bin/activate && python -m pytest tests/ -v --cov=src/twstock_analyzer --cov-report=term 2>&1 | tail -20
  ```
  Expected: "PASSED (X passed)" or equivalent, coverage >70%.
  QA scenarios:
  - Happy: All tests pass → exit code 0, coverage report shows >70%
  - Failure: Any test fails → exit code 1, details printed
  - Edge: CLI test with invalid stock ID → exit code 1, error message in stderr
  Evidence: .omo/evidence/task-14-tw-stock-analyzer-system.txt
  Commit: Y | test: add integration and CLI tests

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [x] F1. **Plan compliance audit** — Verify implementable decisions only:
  - D3 (FinMind + TWSE data sources): ✅ Both sources exist and implement BaseDataSource
  - D4 (Technical analysis: MA, MACD, RSI, KD, BB): ✅ All 5 indicators implemented
  - D6 (SQLite database): ✅ Schema created with PKs, indexes, WAL mode
  - D7 (Cache-Aside pattern): ✅ has_data() check before fetch, upsert after fetch
  - D8 (Free sources prioritized): ✅ FinMind before TWSE in source list
  - D9 (CLI primary + Streamlit optional): ✅ CLI commands exist, Streamlit does NOT block anything
  - D10/D11 (Logging format): ✅ Log file shows correct format: timestamp|level|module|context|status|message|metrics
  - D12 (Fallback chain): ✅ DataLoader iterates [FinMind, TWSE], logs SWITCH on fail
  - D14 (Docker): ✅ Dockerfile + docker-compose.yml exist
  - D15 (Usage docs): ✅ README.md has CLI commands documentation
  - D1/D2/D5 are architectural decisions (positioning/lang/phasing) — verified by F4 (scope fidelity)
  
- [x] F2. **Code quality review** — Automated checks:
  - `ruff check src/` — no errors or warnings
  - `grep -r "api_key\|API_KEY\|password" src/ --include="*.py"` — no hardcoded secrets
  - No `try: ... except: pass` (bare except) patterns
  - `grep -r "^import\|^from" src/ --include="*.py" | head -20` — all imports are from declared dependencies
  
- [x] F3. **Integration smoke test** (requires real FinMind API token from `.env`):
  ```bash
  source /home/jason/projects/venv312/bin/activate && cd /home/jason/projects/TwStockAnalyzer
  python -m twstock_analyzer.cli.main analyze 2330 --indicators ma,rsi --start 2025-01-01 --end 2025-06-30
  ```
  Expected: Rich table with prices + SMA + RSI columns, no errors. 
  Note: This is a manual step if no token is available — agent may skip but must document.
  
- [x] F4. **Scope fidelity** — Confirm Phase 2 features are NOT implemented:
  - `grep -r "backtest\|screening\|strategy\|screener" src/ --include="*.py"` — if found in Phase 1 code, flag as scope creep
  - Verify no `analysis/backtesting/` or `analysis/screening/` directories exist
  - Verify no web server dependencies (Flask, FastAPI) in requirements.txt
  - Verify no order/trading API integrations

## Commit strategy
- chore: project skeleton and dependencies
- feat(logger): add unified logging module
- feat(db): add SQLite schema and repository
- feat(data): add abstract data source interface with retry support
- feat(data): add FinMind data source
- feat(data): add TWSE source and fallback chain
- feat(analysis): add technical analysis module
- feat(analysis): add fundamental analysis module
- feat(analysis): add institutional analysis module
- feat(cli): add CLI commands update/analyze/report/list
- feat(viz): add plotly charting and report module
- feat(streamlit): add optional Streamlit dashboard
- feat(docker): add Docker support and usage docs
- test: add conftest fixtures, integration and CLI tests

## Success criteria
1. `analyze 2330 --indicators ma,macd,rsi,kd,bb` prints a Rich table containing 2330, MA values, RSI values
2. Running `analyze` twice → second run uses SQLite cache (log shows CACHE HIT), no API calls
3. `update --stock 2330 --force` → re-fetches despite cache (log shows MISS forced)
4. Simulate FinMind API failure → auto-fallbacks to TWSE (log shows SWITCH from=finmind to=twse)
5. Invalid stock ID "abc" → clear error "Invalid stock ID format"
6. Log file at data/twstock.log has the correct unified format (timestamp|level|module|context|status|message|metrics)
7. `docker compose run --rm app analyze 2330 --indicators ma` works identically to native CLI
8. Streamlit dashboard shows interactive K-line chart (when started manually)
9. `pytest tests/ -v --cov=src/twstock_analyzer` all pass, coverage >80%
10. SQLite WAL mode is enabled (PRAGMA journal_mode returns 'wal')
