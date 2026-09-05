"""Database metrics for query instrumentation.

Provides metrics for tracking database operations including:
- Query counts by operation and table
- Query duration histograms
- Error tracking
- Connection pool statistics
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics

from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Standard histogram buckets for database query latency (in seconds)
DB_LATENCY_BUCKETS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
)


@dataclass
class PoolStats:
    """Database connection pool statistics.

    Mirrors the stats available from SQLAlchemy's pool.
    """

    open_connections: int
    idle_connections: int
    in_use_connections: int


class DBMetrics:
    """Metrics for database query instrumentation.

    Tracks query counts, durations, errors, and connection pool stats
    with labels for service, operation, table, and status.

    Usage:
        metrics = DBMetrics(meter, "historian")

        # Record a query
        metrics.record_query(
            operation="select",
            table="incidents",
            duration_seconds=0.05,
        )

        # Or use the context manager
        record = metrics.start_query("select", "incidents")
        try:
            result = await db.execute(query)
            record(None)
        except Exception as e:
            record(e)

        # Update pool stats periodically
        metrics.update_pool_stats(PoolStats(
            open_connections=10,
            idle_connections=5,
            in_use_connections=5,
        ))
    """

    def __init__(
        self,
        meter: metrics.Meter,
        service_name: str,
    ) -> None:
        """Initialize database metrics.

        Args:
            meter: OpenTelemetry meter for creating instruments
            service_name: Name of the service using this database
        """
        self._service_name = service_name

        self._query_count = meter.create_counter(
            name="matik_db_queries_total",
            description="Total number of database queries",
            unit="{query}",
        )

        self._query_duration = meter.create_histogram(
            name="matik_db_query_duration_seconds",
            description="Database query duration in seconds",
            unit="s",
            explicit_bucket_boundaries_advisory=DB_LATENCY_BUCKETS,
        )

        self._query_errors = meter.create_counter(
            name="matik_db_query_errors_total",
            description="Total number of database query errors",
            unit="{error}",
        )

        self._open_conns = meter.create_gauge(
            name="matik_db_pool_open_connections",
            description="Number of open database connections",
            unit="{connection}",
        )

        self._idle_conns = meter.create_gauge(
            name="matik_db_pool_idle_connections",
            description="Number of idle database connections",
            unit="{connection}",
        )

        self._in_use_conns = meter.create_gauge(
            name="matik_db_pool_in_use_connections",
            description="Number of database connections currently in use",
            unit="{connection}",
        )

        self._wait_count = meter.create_counter(
            name="matik_db_pool_wait_total",
            description="Total number of times a connection was waited for",
            unit="{wait}",
        )

        self._wait_duration = meter.create_histogram(
            name="matik_db_pool_wait_duration_seconds",
            description="Time spent waiting for a database connection",
            unit="s",
            explicit_bucket_boundaries_advisory=DB_LATENCY_BUCKETS,
        )

    def record_query(
        self,
        operation: str,
        table: str,
        duration_seconds: float,
        error: Exception | None = None,
    ) -> None:
        """Record a database query execution with timing and error tracking.

        Args:
            operation: Query operation (select, insert, update, delete, upsert)
            table: Table name being queried
            duration_seconds: Query duration in seconds
            error: Exception if the query failed
        """
        status = "error" if error is not None else "success"

        attrs: dict[str, Any] = {
            "service": self._service_name,
            "operation": operation,
            "table": table,
            "status": status,
        }

        self._query_count.add(1, attrs)
        self._query_duration.record(duration_seconds, attrs)

        logger.debug(
            "recorded db query metric",
            operation=operation,
            table=table,
            status=status,
            duration_seconds=round(duration_seconds, 3),
        )

        if error is not None:
            self._query_errors.add(1, attrs)
            logger.debug(
                "recorded db query error metric",
                operation=operation,
                table=table,
                error_type=type(error).__name__,
            )

    def update_pool_stats(self, stats: PoolStats) -> None:
        """Update connection pool metrics.

        Args:
            stats: Current pool statistics
        """
        service_attr = {"service": self._service_name}

        self._open_conns.set(stats.open_connections, service_attr)
        self._idle_conns.set(stats.idle_connections, service_attr)
        self._in_use_conns.set(stats.in_use_connections, service_attr)

        logger.debug(
            "recorded db pool stats metric",
            open_connections=stats.open_connections,
            idle_connections=stats.idle_connections,
            in_use_connections=stats.in_use_connections,
        )

    def record_pool_wait(self, duration_seconds: float) -> None:
        """Record time spent waiting for a connection from the pool.

        Args:
            duration_seconds: Wait duration in seconds
        """
        attrs = {"service": self._service_name}
        self._wait_count.add(1, attrs)
        self._wait_duration.record(duration_seconds, attrs)

        logger.debug(
            "recorded db pool wait metric",
            duration_seconds=round(duration_seconds, 3),
        )

    def start_query(
        self,
        operation: str,
        table: str,
    ) -> Callable[[Exception | None], None]:
        """Start timing a query and return a function to record completion.

        Usage:
            record = metrics.start_query("select", "incidents")
            try:
                result = await db.execute(query)
                record(None)
            except Exception as e:
                record(e)
                raise

        Args:
            operation: Query operation (select, insert, update, delete, upsert)
            table: Table name being queried

        Returns:
            A function to call when the query completes with optional error.
        """
        start = time.perf_counter()

        def record(error: Exception | None = None) -> None:
            duration = time.perf_counter() - start
            self.record_query(operation, table, duration, error)

        return record
