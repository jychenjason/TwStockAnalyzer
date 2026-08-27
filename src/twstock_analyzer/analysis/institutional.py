import pandas as pd
import sqlite3
from typing import Optional


class InstitutionalAnalyzer:
    """Compute institutional trading metrics from DB data."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path

    def foreign_investors(self, stock_id: str, days: int = 30) -> pd.DataFrame:
        """Foreign investor buy/sell/net. Returns DataFrame with date, buy, sell, net."""
        conn = sqlite3.connect(self.db_path or 'data/twstock.db')
        try:
            df = pd.read_sql_query(
                """SELECT date, foreign_buy, foreign_sell, foreign_net
                   FROM institutional_trading
                   WHERE stock_id = ?
                   ORDER BY date DESC LIMIT ?""",
                conn, params=(stock_id, days)
            )
            if df.empty:
                return pd.DataFrame(columns=['date', 'foreign_buy', 'foreign_sell', 'foreign_net'])
            return df.sort_values('date', ascending=True).reset_index(drop=True)
        finally:
            conn.close()

    def mutual_funds(self, stock_id: str, days: int = 30) -> pd.DataFrame:
        """Fund (投信) investor buy/sell/net. Returns DataFrame with date, buy, sell, net."""
        conn = sqlite3.connect(self.db_path or 'data/twstock.db')
        try:
            df = pd.read_sql_query(
                """SELECT date, fund_buy, fund_sell, fund_net
                   FROM institutional_trading
                   WHERE stock_id = ?
                   ORDER BY date DESC LIMIT ?""",
                conn, params=(stock_id, days)
            )
            if df.empty:
                return pd.DataFrame(columns=['date', 'fund_buy', 'fund_sell', 'fund_net'])
            return df.sort_values('date', ascending=True).reset_index(drop=True)
        finally:
            conn.close()

    def dealers(self, stock_id: str, days: int = 30) -> pd.DataFrame:
        """Dealer (自營商) buy/sell/net. Returns DataFrame with date, buy, sell, net."""
        conn = sqlite3.connect(self.db_path or 'data/twstock.db')
        try:
            df = pd.read_sql_query(
                """SELECT date, dealer_buy, dealer_sell, dealer_net
                   FROM institutional_trading
                   WHERE stock_id = ?
                   ORDER BY date DESC LIMIT ?""",
                conn, params=(stock_id, days)
            )
            if df.empty:
                return pd.DataFrame(columns=['date', 'dealer_buy', 'dealer_sell', 'dealer_net'])
            return df.sort_values('date', ascending=True).reset_index(drop=True)
        finally:
            conn.close()

    def shareholding_distribution(self, stock_id: str, date: str = None) -> pd.DataFrame:
        """Shareholding distribution by level."""
        conn = sqlite3.connect(self.db_path or 'data/twstock.db')
        try:
            if date:
                df = pd.read_sql_query(
                    "SELECT * FROM institutional_trading WHERE stock_id = ? AND date = ?",
                    conn, params=(stock_id, date)
                )
            else:
                df = pd.read_sql_query(
                    "SELECT * FROM institutional_trading WHERE stock_id = ? ORDER BY date DESC LIMIT 1",
                    conn, params=(stock_id,)
                )
            return df
        finally:
            conn.close()

    def net_buy_trend(self, stock_id: str, days: int = 30) -> Optional[str]:
        """Compute net buy/sell trend over period. Returns 'accumulating', 'distributing', or 'neutral'."""
        conn = sqlite3.connect(self.db_path or 'data/twstock.db')
        try:
            df = pd.read_sql_query(
                "SELECT total_net FROM institutional_trading WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
                conn, params=(stock_id, days)
            )
            if df.empty or df['total_net'].sum() == 0:
                return None
            total = df['total_net'].sum()
            if total > 0:
                return 'accumulating'
            elif total < 0:
                return 'distributing'
            return 'neutral'
        finally:
            conn.close()
