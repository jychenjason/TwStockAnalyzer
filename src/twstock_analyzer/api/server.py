from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware


def _df_to_records(df):
    """Convert DataFrame to JSON-safe records, replacing NaN with None."""
    if df.empty:
        return []
    records = df.to_dict(orient="records")
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, float) and v != v:  # NaN check
                rec[k] = None
    return records


def create_app() -> FastAPI:
    app = FastAPI(title="TwStockAnalyzer API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/stocks")
    def list_stocks():
        import os
        import sqlite3

        import pandas as pd

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")
        try:
            conn = sqlite3.connect(db_path)
            df = pd.read_sql_query(
                "SELECT stock_id, MAX(date) as latest_date, MAX(close) as latest_close, COUNT(*) as data_points "
                "FROM daily_prices GROUP BY stock_id ORDER BY latest_close DESC",
                conn,
            )
            conn.close()
            return _df_to_records(df)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # GET /stocks/{stock_id}/prices
    # ------------------------------------------------------------------
    @app.get("/stocks/{stock_id}/prices")
    def get_prices(
        stock_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: Optional[int] = None,
    ):
        import os
        import sqlite3

        import pandas as pd

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")
        query = "SELECT * FROM daily_prices WHERE stock_id = ?"
        params: list = [stock_id]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        try:
            conn = sqlite3.connect(db_path)
            df = pd.read_sql_query(query, conn, params=params)
            conn.close()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        if df.empty:
            raise HTTPException(status_code=404, detail=f"No price data for stock {stock_id}")

        # Coerce date column to string (may be bytes or datetime)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        # Coerce all object columns to numeric, skipping date
        for col in df.select_dtypes(include=["object"]).columns:
            if col != "date":
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Convert datetime columns to strings
        for col in df.select_dtypes(include=["datetime"]).columns:
            df[col] = df[col].dt.strftime("%Y-%m-%d")

        return _df_to_records(df)

    # ------------------------------------------------------------------
    # GET /stocks/{stock_id}/analysis
    # ------------------------------------------------------------------
    @app.get("/stocks/{stock_id}/analysis")
    def get_analysis(
        stock_id: str,
        indicators: Optional[str] = "ma,rsi",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        import os
        import sqlite3

        import pandas as pd

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")

        query = "SELECT * FROM daily_prices WHERE stock_id = ?"
        params: list = [stock_id]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"

        try:
            conn = sqlite3.connect(db_path)
            df = pd.read_sql_query(query, conn, params=params)
            conn.close()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for stock {stock_id}")

        # Coerce date column
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")

        # Coerce all object columns to numeric, skipping date
        for col in df.select_dtypes(include=["object"]).columns:
            if col != "date":
                df[col] = pd.to_numeric(df[col], errors="coerce")

        indicator_list = [i.strip().lower() for i in indicators.split(",")]

        from twstock_analyzer.analysis.technical import TechnicalAnalyzer

        analyzer = TechnicalAnalyzer()

        for ind in indicator_list:
            if ind == "ma":
                df = analyzer.sma(df)
            elif ind == "macd":
                df = analyzer.macd(df)
            elif ind == "rsi":
                df = analyzer.rsi(df)
            elif ind == "kd":
                df = analyzer.kd(df)
            elif ind == "bb":
                df = analyzer.bollinger_bands(df)

        for col in df.select_dtypes(include=["datetime"]).columns:
            df[col] = df[col].dt.strftime("%Y-%m-%d")

        result = _df_to_records(df)
        return {"stock_id": stock_id, "indicators": indicator_list, "data": result}

    # ------------------------------------------------------------------
    # GET /stocks/{stock_id}/fundamentals
    # ------------------------------------------------------------------
    @app.get("/stocks/{stock_id}/fundamentals")
    def get_fundamentals(stock_id: str):
        import os
        import sqlite3

        import pandas as pd

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            pe_row = conn.execute(
                "SELECT pe_ratio FROM fundamentals WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1",
                (stock_id,),
            ).fetchone()
            pe_ratio = float(pe_row["pe_ratio"]) if pe_row and pe_row["pe_ratio"] is not None else None

            # eps / revenue / revenue_yoy / roe 這四欄在 `fundamentals` 裡沒有
            # 任何來源會填。照著讀會回傳一串值全為 null 的物件——比空陣列更糟，
            # 呼叫端會判定「有資料」。改讀各自真正的來源表。
            from twstock_analyzer.screening.financials import ensure_financial_tables

            ensure_financial_tables(conn)

            eps_df = pd.read_sql_query(
                "SELECT period, eps, revenue AS quarter_revenue FROM quarterly_financials"
                " WHERE stock_id = ? ORDER BY period DESC",
                conn,
                params=(stock_id,),
            )
            eps_trend = _df_to_records(eps_df)

            rev_df = pd.read_sql_query(
                "SELECT month, revenue, revenue_yoy FROM monthly_revenue"
                " WHERE stock_id = ? ORDER BY month DESC",
                conn,
                params=(stock_id,),
            )
            revenue = _df_to_records(rev_df)

            # ROE 目前沒有免費來源，誠實回空陣列。
            roe_df = pd.read_sql_query(
                "SELECT report_date, roe FROM fundamentals"
                " WHERE stock_id = ? AND roe IS NOT NULL ORDER BY report_date DESC",
                conn,
                params=(stock_id,),
            )
            roe = _df_to_records(roe_df)

            conn.close()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return {
            "stock_id": stock_id,
            "pe_ratio": pe_ratio,
            "eps_trend": eps_trend,
            "roe": roe,
            "revenue": revenue,
        }

    # ------------------------------------------------------------------
    # GET /stocks/{stock_id}/institutional
    # ------------------------------------------------------------------
    @app.get("/stocks/{stock_id}/institutional")
    def get_institutional(
        stock_id: str,
        days: Optional[int] = 30,
    ):
        import os
        import sqlite3

        import pandas as pd

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")

        try:
            conn = sqlite3.connect(db_path)

            foreign_df = pd.read_sql_query(
                "SELECT date, foreign_buy, foreign_sell, foreign_net "
                "FROM institutional_trading WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
                conn,
                params=(stock_id, days),
            )
            foreign = _df_to_records(foreign_df)

            fund_df = pd.read_sql_query(
                "SELECT date, fund_buy, fund_sell, fund_net, total_net "
                "FROM institutional_trading WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
                conn,
                params=(stock_id, days),
            )
            funds = _df_to_records(fund_df)

            dealer_df = pd.read_sql_query(
                "SELECT date, dealer_buy, dealer_sell, dealer_net "
                "FROM institutional_trading WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
                conn,
                params=(stock_id, days),
            )
            dealers = _df_to_records(dealer_df)

            conn.close()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return {
            "stock_id": stock_id,
            "foreign_investor": foreign,
            "investment_trust": funds,
            "dealer": dealers,
        }

    # ------------------------------------------------------------------
    # POST /stocks/{stock_id}/update
    # ------------------------------------------------------------------
    @app.post("/stocks/{stock_id}/update")
    def update_stock(
        stock_id: str,
        data_type: str = "daily",
    ):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable, "-m", "twstock_analyzer.cli.main",
                "update",
                "--stock", stock_id,
                "--type", data_type,
                "--force",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Update failed: {result.stderr}",
            )

        return {
            "stock_id": stock_id,
            "data_type": data_type,
            "status": "success",
            "output": result.stdout,
        }

    # ------------------------------------------------------------------
    # POST /stocks/{stock_id}/report
    # ------------------------------------------------------------------
    @app.post("/stocks/{stock_id}/report")
    def generate_report(
        stock_id: str,
        format: str = "html",
    ):
        import os
        import tempfile
        import sqlite3
        from datetime import datetime, timedelta

        import pandas as pd

        from twstock_analyzer.analysis.technical import TechnicalAnalyzer
        from twstock_analyzer.visualization.report import generate_report as _gen_report

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")

        today = datetime.now().strftime("%Y-%m-%d")
        year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        try:
            conn = sqlite3.connect(db_path)
            prices = pd.read_sql_query(
                "SELECT * FROM daily_prices WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
                conn,
                params=(stock_id, year_ago, today),
            )
            conn.close()
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        if prices.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No price data for {stock_id} in [{year_ago}, {today}]",
            )

        for col in prices.select_dtypes(include=["object"]).columns:
            prices[col] = pd.to_numeric(prices[col], errors="coerce")

        tech = TechnicalAnalyzer()
        prices = tech.calculate_all(prices)

        data_dict = {
            "prices": prices,
            "technical": prices,
            "stock_id": stock_id,
            "generated_at": datetime.now().isoformat(),
        }

        tmp_dir = tempfile.gettempdir()
        filename = f"report-{stock_id}.{format}"
        output_path = os.path.join(tmp_dir, filename)

        try:
            _gen_report(stock_id, data_dict, output_path, format=format)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        return {
            "stock_id": stock_id,
            "format": format,
            "output": output_path,
            "status": "generated",
        }

    # ------------------------------------------------------------------
    # POST /screen
    # ------------------------------------------------------------------
    @app.post("/screen")
    def run_screen(
        pe_min: Optional[float] = None,
        pe_max: Optional[float] = None,
        rsi_min: Optional[float] = None,
        rsi_max: Optional[float] = None,
        volume_min: Optional[int] = None,
        volume_spike: Optional[float] = None,
        volume_spike_window: int = 20,
        spike_direction: Optional[str] = None,
        spike_direction_band: float = 1.0,
        ma_crossover: Optional[str] = None,
        ma_direction: str = "up",
        ma_within: int = 1,
        ma_trend_align: bool = True,
        ma_trend_window: int = 3,
        eps_min: Optional[float] = None,
        eps_growth_min: Optional[float] = None,
        revenue_yoy_min: Optional[float] = None,
        dividend_yield_min: Optional[float] = None,
    ):
        import os

        from twstock_analyzer.screening.screener import (
            MissingDataError,
            ScreenCriteria,
            run_screen as _run_screen,
        )

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")

        criteria = ScreenCriteria(
            pe_min=pe_min,
            pe_max=pe_max,
            rsi_min=rsi_min,
            rsi_max=rsi_max,
            volume_min=volume_min,
            volume_spike=volume_spike,
            volume_spike_window=volume_spike_window,
            spike_direction=spike_direction,
            spike_direction_band=spike_direction_band,
            ma_crossover=ma_crossover,
            ma_crossover_direction=ma_direction,
            ma_crossover_within=ma_within,
            ma_trend_align=ma_trend_align,
            ma_trend_window=ma_trend_window,
            eps_min=eps_min,
            eps_growth_min=eps_growth_min,
            revenue_yoy_min=revenue_yoy_min,
            dividend_yield_min=dividend_yield_min,
        )

        try:
            df = _run_screen(criteria, db_path=db_path)
        except MissingDataError as exc:
            # 409：請求本身沒錯，是伺服器端還沒有這份資料。回 200 空陣列會讓
            # 呼叫端把「還沒抓」當成「沒有符合的股票」。
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return {
            "criteria": {
                "pe_min": pe_min,
                "pe_max": pe_max,
                "rsi_min": rsi_min,
                "rsi_max": rsi_max,
                "volume_min": volume_min,
                "volume_spike": volume_spike,
                "volume_spike_window": volume_spike_window,
                "spike_direction": spike_direction,
                "ma_crossover": ma_crossover,
                "ma_direction": ma_direction,
                "ma_within": ma_within,
                "ma_trend_align": ma_trend_align,
                "ma_trend_window": ma_trend_window,
            },
            "matches": len(df),
            "results": _df_to_records(df),
        }

    # ------------------------------------------------------------------
    # POST /backtest
    # ------------------------------------------------------------------
    @app.post("/backtest")
    def run_backtest(
        stocks: str = "2330",
        capital: float = 1000000.0,
        signal: str = "ma_cross",
        start_date: str = "2024-01-01",
        end_date: Optional[str] = None,
    ):
        import os
        from datetime import datetime

        from twstock_analyzer.backtesting.engine import run_backtest as _run_backtest
        from twstock_analyzer.backtesting.portfolio import PortfolioConfig

        db_path = os.environ.get("TWSTOCK_DB", "data/twstock.db")

        stock_ids = [s.strip() for s in stocks.split(",") if s.strip()]
        end = end_date or datetime.now().strftime("%Y-%m-%d")
        config = PortfolioConfig(initial_capital=capital)

        result = _run_backtest(
            stock_ids=stock_ids,
            config=config,
            start_date=start_date,
            end_date=end,
            signal_method=signal,
            db_path=db_path,
        )

        m = result.metrics
        equity_curve_data = []
        if not result.equity_curve.empty:
            for idx, val in result.equity_curve.items():
                date_str = str(idx)[:10] if hasattr(idx, "__str__") else str(idx)
                equity_curve_data.append({"date": date_str, "value": float(val)})

        return {
            "stocks": stock_ids,
            "signal": signal,
            "start_date": start_date,
            "end_date": end,
            "initial_capital": capital,
            "metrics": {
                "total_return": float(m.total_return),
                "cagr": float(m.cagr),
                "volatility": float(m.volatility),
                "sharpe_ratio": float(m.sharpe_ratio),
                "max_drawdown": float(m.max_drawdown),
                "win_rate": float(m.win_rate),
                "total_trades": int(m.total_trades),
            },
            "equity_curve": equity_curve_data,
        }

    return app


app = create_app()
