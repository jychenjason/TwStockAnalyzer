"""Replay -- 逐日回放與模擬交易。

Replay Session 是一次回放練習的完整紀錄；Cursor 是它當下所在的交易日。
本模組不匯入任何介面框架，也不發出任何網路請求。
"""

from .service import (
    CoverageError,
    ReplaySession,
    SessionNotFoundError,
    create_session,
    delete_session,
    list_sessions,
    load_session,
)

__all__ = [
    "CoverageError",
    "ReplaySession",
    "SessionNotFoundError",
    "create_session",
    "delete_session",
    "list_sessions",
    "load_session",
]
