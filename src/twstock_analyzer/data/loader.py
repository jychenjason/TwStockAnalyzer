"""Data source abstraction, validation, retry logic, and DataLoader fallback chain."""

from __future__ import annotations

import functools
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from twstock_analyzer.utils.logger import get_logger  # noqa: F401


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DataFetchError(Exception):
    """Base exception for data fetch failures."""


class DataFallbackError(DataFetchError):
    """Raised when all configured data sources fail."""


# ---------------------------------------------------------------------------
# Retry mixin / decorator
# ---------------------------------------------------------------------------

RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1  # seconds


def _retry_decorator(func):
    """Decorate *func* with exponential-backoff retry (3 attempts, 1s/2s/4s)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(RETRY_MAX_ATTEMPTS):
            try:
                return func(*args, **kwargs)
            except DataFetchError:
                raise  # propagate immediately — don't retry fetch errors
            except Exception as exc:
                last_exc = exc
                if attempt < RETRY_MAX_ATTEMPTS - 1:
                    # 退避是重點，不是禮貌。TWSE 對連續請求回 428 限流，
                    # 立刻重試三次只會拿到三個 428。
                    time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
                    continue
                raise
        raise last_exc  # pragma: no cover

    return wrapper


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_STOCK_ID_RE = re.compile(r'^\d{4}$')


def validate_stock_id(stock_id: str) -> str:
    """Validate a 4-digit numeric stock ID.

    Returns the validated stock ID string.
    Raises ``ValueError`` if the ID is not exactly 4 digits.
    """
    if not _STOCK_ID_RE.match(str(stock_id)):
        raise ValueError(
            f'Invalid stock ID "{stock_id}": expected 4-digit numeric format'
        )
    return stock_id


def validate_date(date_str: str) -> str:
    """Parse and normalize a date string to ``YYYY-MM-DD`` format.

    Accepts ``YYYY-MM-DD``, ``YYYY/MM/DD``, or ``YYYYMMDD`` inputs.
    Raises ``ValueError`` if the date cannot be parsed.
    """
    date_str = str(date_str).strip()

    # Try common formats
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    raise ValueError(f'Invalid date "{date_str}": expected YYYY-MM-DD format')


# ---------------------------------------------------------------------------
# Base data source
# ---------------------------------------------------------------------------

class BaseDataSource(ABC):
    """Abstract base class for all data sources.

    Subclasses must implement ``name``, ``fetch_daily``,
    ``fetch_fundamentals``, and ``fetch_institutional``.

    All fetch methods are wrapped with a retry decorator that catches
    generic ``Exception`` and retries up to 3 times with exponential
    backoff (1 s, 2 s, 4 s).  ``DataFetchError`` is **not** retried
    because it signals a known, non-transient failure.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name (used in logging)."""

    @abstractmethod
    def fetch_daily(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch daily price data.

        Parameters
        ----------
        stock_id : str
            Validated 4-digit stock identifier.
        start_date : str
            Inclusive start date (YYYY-MM-DD).
        end_date : str
            Inclusive end date (YYYY-MM-DD).

        Returns
        -------
        pd.DataFrame
            Columns: date, open, high, low, close, volume (plus optional).
        """

    @abstractmethod
    def fetch_fundamentals(self, stock_id: str, period: str) -> pd.DataFrame:
        """Fetch fundamental data.

        Parameters
        ----------
        stock_id : str
            Validated 4-digit stock identifier.
        period : str
            Period identifier, e.g. ``"2024Q1"``.

        Returns
        -------
        pd.DataFrame
        """

    @abstractmethod
    def fetch_institutional(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch institutional trading data for a date range.

        Parameters
        ----------
        stock_id : str
            Validated 4-digit stock identifier.
        start_date : str
            Inclusive start date (YYYY-MM-DD).
        end_date : str
            Inclusive end date (YYYY-MM-DD).

        Returns
        -------
        pd.DataFrame
        """

    @classmethod
    def should_skip(cls, env_check: dict | None = None) -> bool:
        """Return ``True`` if this source should be skipped (e.g. missing credentials).

        Override in subclasses.  Default is ``False``.
        """
        return False

    def fetch_stock_list(self) -> list[dict]:
        """Return a list of all available stocks as ``{'stock_id': str, 'name': str}`` dicts.

        Subclasses that support this should override; default raises
        ``NotImplementedError``.
        """
        raise NotImplementedError(f'{type(self).__name__} does not support stock list')

    def fetch_all_fundamentals(self) -> pd.DataFrame:
        """Fetch fundamental data for ALL stocks in a single call.

        Subclasses that support this should override; default raises
        ``NotImplementedError``.
        """
        raise NotImplementedError(f'{type(self).__name__} does not support batch all-fundamentals')

    # -- retry wrapping -----------------------------------------------------

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Wrap concrete fetch methods with retry (skip abstract stubs)
        for attr in ('fetch_daily', 'fetch_fundamentals', 'fetch_institutional'):
            original = getattr(cls, attr)
            # Only wrap if not an abstract placeholder
            if not getattr(original, '__is_abstract__', False):
                setattr(cls, attr, _retry_decorator(original))


# ---------------------------------------------------------------------------
# DataLoader (fallback chain)
# ---------------------------------------------------------------------------

class DataLoader:
    """Orchestrates data fetching across multiple sources with fallback.

    Parameters
    ----------
    sources : list[BaseDataSource]
        Source instances ordered by priority (first = preferred).
    """

    def __init__(self, sources: list[BaseDataSource]) -> None:
        self.sources = sources

    def get_stock_list(self, logger: logging.Logger | None = None) -> list[dict]:
        """Fetch the list of all available stocks from the first capable source.

        Returns a list of ``{'stock_id': str, 'name': str}`` dicts.
        Returns empty list if no source supports stock listing.
        """
        if logger is None:
            logger = logging.getLogger(__name__)

        for source in self.sources:
            if source.should_skip():
                continue
            try:
                stocks = source.fetch_stock_list()
                if stocks:
                    logger.info(
                        'Fetched %d stocks from %s',
                        len(stocks), source.name,
                        extra={'status': 'SUCCESS'},
                    )
                    return stocks
            except NotImplementedError:
                continue
            except Exception as exc:
                logger.warning(
                    '%s stock list failed: %s',
                    source.name, exc,
                    extra={'status': 'FAILED'},
                )
                continue

        logger.warning('No source could provide stock list', extra={'status': 'FAIL'})
        return []

    def get_all_fundamentals(self, logger: logging.Logger | None = None) -> pd.DataFrame | None:
        """Fetch fundamental data for ALL stocks in one API call.

        Returns a DataFrame with columns: ``stock_id``, ``date``,
        ``pe_ratio``, ``dividend_yield``, or ``None`` if no source supports
        batch fundamental fetch.
        """
        if logger is None:
            logger = logging.getLogger(__name__)

        for source in self.sources:
            if source.should_skip():
                continue
            try:
                df = source.fetch_all_fundamentals()
                if df is not None and not df.empty:
                    logger.info(
                        'Fetched %d fundamental rows from %s',
                        len(df), source.name,
                        extra={'status': 'SUCCESS'},
                    )
                    return df
            except (NotImplementedError, AttributeError):
                continue
            except Exception as exc:
                logger.warning(
                    '%s all-fundamentals failed: %s',
                    source.name, exc,
                    extra={'status': 'FAILED'},
                )
                continue

        logger.warning('No source could provide all-fundamentals', extra={'status': 'FAIL'})
        return None

    def get_data(
        self,
        stock_id: str,
        data_type: str,
        start_date: str,
        end_date: str | None = None,
        logger: logging.Logger | None = None,
    ) -> pd.DataFrame:
        """Fetch data iterating sources in priority order.

        Parameters
        ----------
        stock_id : str
            4-digit stock identifier (will be validated).
        data_type : str
            One of ``"daily"``, ``"fundamentals"``, ``"institutional"``.
        start_date : str
            Start date (YYYY-MM-DD).
        end_date : str, optional
            End date (YYYY-MM-DD).  Ignored for ``"fundamentals"`` /
            ``"institutional"``.
        logger : logging.Logger, optional
            Logger instance.  If *None*, a basic logger is created.

        Returns
        -------
        pd.DataFrame

        Raises
        ------
        DataFallbackError
            When every source fails or is skipped.
        """
        stock_id = validate_stock_id(stock_id)

        if end_date is None:
            end_date = start_date

        start_date = validate_date(start_date)
        end_date = validate_date(end_date)

        if logger is None:
            logger = logging.getLogger(__name__)

        _fundamental_key = data_type if data_type != 'fundamental' else 'fundamentals'

        method_map = {
            'daily': 'fetch_daily',
            'fundamentals': 'fetch_fundamentals',
            'institutional': 'fetch_institutional',
            'dividend': 'fetch_dividends',
        }
        method_name = method_map.get(_fundamental_key)
        if method_name is None:
            raise ValueError(f'Unknown data_type: {data_type!r}')

        failed_sources: list[str] = []

        for source in self.sources:
            if source.should_skip():
                logger.info(
                    '%s: skipped (credentials missing)',
                    source.name,
                    extra={'status': 'SKIP'},
                )
                continue

            logger.info(
                '%s: START fetching %s [%s..%s]',
                source.name,
                data_type,
                start_date,
                end_date,
                extra={'status': 'START'},
            )

            try:
                method = getattr(source, method_name)
                if _fundamental_key == 'daily':
                    result = method(stock_id, start_date, end_date)
                elif _fundamental_key == 'fundamentals':
                    result = method(stock_id, start_date)
                elif _fundamental_key in ('institutional', 'dividend'):
                    result = method(stock_id, start_date, end_date)
                else:
                    result = method(stock_id, start_date)

                logger.info(
                    '%s: SUCCESS (%d rows)',
                    source.name,
                    len(result),
                    extra={'status': 'SUCCESS'},
                )
                return result

            except DataFetchError as exc:
                failed_sources.append(source.name)
                logger.warning(
                    '%s: FAILED – %s',
                    source.name,
                    exc,
                    extra={'status': 'FAILED'},
                )
                continue
            except Exception as exc:
                failed_sources.append(source.name)
                logger.error(
                    '%s: ERROR – %s',
                    source.name,
                    exc,
                    extra={'status': 'ERROR'},
                )
                continue

        msg = f'All sources failed for {data_type} [{stock_id}]: {failed_sources}'
        logger.error(msg, extra={'status': 'ALL_FAIL'})
        raise DataFallbackError(msg, failed_sources)
