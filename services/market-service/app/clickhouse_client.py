"""ClickHouse access layer for market-service.

Wraps clickhouse-connect with a lazy, retrying connection, schema bootstrap
(MergeTree tables), deterministic seeding, and query helpers for the three
MarketService RPCs.
"""

from __future__ import annotations

import math
import time
from typing import Any

import structlog


def _safe_float(value: Any) -> float:
    """Coerce to float, mapping None/NaN/inf to 0.0.

    quantileExact() over an empty match set returns NaN; `float(x or 0.0)` does
    NOT catch it (NaN is truthy), so NaN would crash round()/int() downstream and
    serialize as invalid JSON. This is the single guard for all aggregates.
    """
    try:
        result = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(result) or math.isinf(result):
        return 0.0
    return result

from app.config import settings
from app.seed import SEED_COLUMNS, generate_listings, listings_as_rows

try:
    import clickhouse_connect
    from clickhouse_connect.driver.client import Client
except ImportError:  # Allows local parser scripts to run before deps are installed.
    clickhouse_connect = None
    Client = Any  # type: ignore

log = structlog.get_logger(__name__)


CREATE_DB_SQL = "CREATE DATABASE IF NOT EXISTS {db}"

CREATE_LISTINGS_SQL = """
CREATE TABLE IF NOT EXISTS {db}.land_listings (
    id              String,
    region          LowCardinality(String),
    category        LowCardinality(String),
    allowed_use     String,
    area            Float64,
    price           Float64,
    price_per_sqm   Float64,
    lat             Float64,
    lng             Float64,
    source          LowCardinality(String),
    listed_at       DateTime
) ENGINE = MergeTree()
ORDER BY (region, category, listed_at)
"""

CREATE_TRANSACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS {db}.land_transactions (
    id              String,
    region          LowCardinality(String),
    category        LowCardinality(String),
    area            Float64,
    price           Float64,
    price_per_sqm   Float64,
    closed_at       DateTime
) ENGINE = MergeTree()
ORDER BY (region, category, closed_at)
"""


class ClickHouseClient:
    def __init__(self) -> None:
        self._client: Client | None = None
        self._ready = False

    # ── connection ────────────────────────────────────────────────────────────
    def connect(self) -> Client:
        """Connect lazily, retrying with backoff (ClickHouse may start slowly)."""

        if self._client is not None:
            return self._client
        if clickhouse_connect is None:
            raise RuntimeError("clickhouse-connect is not installed")

        last_exc: Exception | None = None
        for attempt in range(1, settings.clickhouse_connect_retries + 1):
            try:
                client = clickhouse_connect.get_client(
                    host=settings.clickhouse_host,
                    port=settings.clickhouse_http_port,
                    username=settings.clickhouse_user,
                    password=settings.clickhouse_password,
                    # Connect without a database first so we can CREATE it.
                    database="default",
                    connect_timeout=10,
                    send_receive_timeout=60,
                )
                client.command("SELECT 1")
                self._client = client
                log.info("clickhouse_connected", host=settings.clickhouse_host, attempt=attempt)
                return client
            except Exception as exc:  # noqa: BLE001 - retry on any connection error
                last_exc = exc
                log.warning(
                    "clickhouse_connect_retry",
                    attempt=attempt,
                    retries=settings.clickhouse_connect_retries,
                    error=str(exc),
                )
                time.sleep(settings.clickhouse_connect_backoff)

        raise RuntimeError(f"Could not connect to ClickHouse after retries: {last_exc}")

    def _db(self) -> str:
        return settings.clickhouse_db

    # ── schema + seed ───────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        client = self.connect()
        db = self._db()
        client.command(CREATE_DB_SQL.format(db=db))
        client.command(CREATE_LISTINGS_SQL.format(db=db))
        client.command(CREATE_TRANSACTIONS_SQL.format(db=db))
        log.info("clickhouse_schema_ready", db=db)

    def _listings_count(self) -> int:
        client = self.connect()
        result = client.command(f"SELECT count() FROM {self._db()}.land_listings")
        try:
            return int(result)
        except (TypeError, ValueError):
            return 0

    def seed_if_empty(self) -> int:
        """Insert deterministic synthetic listings if the table is empty.

        Returns the number of rows inserted (0 if already populated/disabled).
        """

        if not settings.market_seed_enabled:
            log.info("market_seed_disabled")
            return 0

        existing = self._listings_count()
        if existing > 0:
            log.info("market_seed_skipped", existing=existing)
            return 0

        listings = generate_listings(settings.market_seed_count)
        rows = listings_as_rows(listings)
        client = self.connect()
        client.insert(
            table="land_listings",
            data=rows,
            column_names=SEED_COLUMNS,
            database=self._db(),
        )
        log.info("market_seed_inserted", count=len(rows))
        return len(rows)

    def bootstrap(self) -> None:
        self.ensure_schema()
        self.seed_if_empty()
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    # ── query helpers ───────────────────────────────────────────────────────────
    def _query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[tuple]:
        client = self.connect()
        result = client.query(sql, parameters=parameters or {})
        return list(result.result_rows)

    def price_stats(self, region: str, category: str) -> dict[str, Any]:
        """p25 / median / p75 / avg / sample_size over region+category."""

        where, params = _region_category_where(region, category)
        sql = f"""
            SELECT
                quantileExact(0.25)(price_per_sqm) AS p25,
                quantileExact(0.50)(price_per_sqm) AS median,
                quantileExact(0.75)(price_per_sqm) AS p75,
                avg(price_per_sqm)                 AS avg,
                count()                            AS sample_size
            FROM {self._db()}.land_listings
            {where}
        """
        rows = self._query_rows(sql, params)
        if not rows:
            return {"p25": 0.0, "median": 0.0, "p75": 0.0, "avg": 0.0, "sample_size": 0}
        p25, median, p75, avg, sample_size = rows[0]
        return {
            "p25": _safe_float(p25),
            "median": _safe_float(median),
            "p75": _safe_float(p75),
            "avg": _safe_float(avg),
            "sample_size": int(sample_size or 0),
        }

    def analysis_aggregates(self, region: str, category: str, allowed_use: str = "") -> dict[str, Any]:
        """Median/avg ppsqm, count, and recency counts for market analysis.

        recent_count = listings in the last 180 days; total_count overall.
        """

        where, params = _region_category_where(region, category)
        if allowed_use:
            where += " AND allowed_use = {allowed_use:String}"
            params["allowed_use"] = allowed_use
        sql = f"""
            SELECT
                quantileExact(0.50)(price_per_sqm) AS median,
                avg(price_per_sqm)                 AS avg,
                count()                            AS total_count,
                countIf(listed_at >= now() - INTERVAL 180 DAY) AS recent_count
            FROM {self._db()}.land_listings
            {where}
        """
        rows = self._query_rows(sql, params)
        if not rows:
            return {"median": 0.0, "avg": 0.0, "total_count": 0, "recent_count": 0}
        median, avg, total_count, recent_count = rows[0]
        return {
            "median": _safe_float(median),
            "avg": _safe_float(avg),
            "total_count": int(total_count or 0),
            "recent_count": int(recent_count or 0),
        }

    def trend_buckets(self, region: str, category: str) -> dict[str, float]:
        """Median ppsqm for an older vs. a recent window, for trend detection.

        recent  = last 180 days, older = 180-360 days ago.
        """

        where, params = _region_category_where(region, category)
        sql = f"""
            SELECT
                quantileExactIf(0.50)(price_per_sqm, listed_at >= now() - INTERVAL 180 DAY) AS recent_median,
                quantileExactIf(0.50)(price_per_sqm,
                    listed_at < now() - INTERVAL 180 DAY
                    AND listed_at >= now() - INTERVAL 360 DAY) AS older_median
            FROM {self._db()}.land_listings
            {where}
        """
        rows = self._query_rows(sql, params)
        if not rows:
            return {"recent_median": 0.0, "older_median": 0.0}
        recent_median, older_median = rows[0]
        return {
            "recent_median": _safe_float(recent_median),
            "older_median": _safe_float(older_median),
        }

    def comparables(
        self,
        region: str,
        category: str,
        area_min: float,
        area_max: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Comparable listings filtered by region/category/area, recent first."""

        clauses: list[str] = []
        params: dict[str, Any] = {}
        if region:
            clauses.append("region = {region:String}")
            params["region"] = region
        if category:
            clauses.append("category = {category:String}")
            params["category"] = category
        if area_min and area_min > 0:
            clauses.append("area >= {area_min:Float64}")
            params["area_min"] = float(area_min)
        if area_max and area_max > 0:
            clauses.append("area <= {area_max:Float64}")
            params["area_max"] = float(area_max)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params["limit"] = int(limit) if limit and limit > 0 else 20

        sql = f"""
            SELECT id, region, allowed_use, area, price, price_per_sqm, source, listed_at
            FROM {self._db()}.land_listings
            {where}
            ORDER BY listed_at DESC
            LIMIT {{limit:UInt32}}
        """
        rows = self._query_rows(sql, params)
        out: list[dict[str, Any]] = []
        for row in rows:
            cid, rg, allowed_use, area, price, ppsqm, source, listed_at = row
            address = f"{rg}, {allowed_use}" if allowed_use else rg
            out.append(
                {
                    "id": cid,
                    "address": address,
                    "area": float(area or 0.0),
                    "price": float(price or 0.0),
                    "price_per_sqm": float(ppsqm or 0.0),
                    "source": source or "",
                    "listed_at": listed_at.isoformat() if hasattr(listed_at, "isoformat") else str(listed_at),
                }
            )
        return out


def _region_category_where(region: str, category: str) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if region:
        clauses.append("region = {region:String}")
        params["region"] = region
    if category:
        clauses.append("category = {category:String}")
        params["category"] = category
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# Module-level singleton, mirroring the get_client() pattern in other services.
_client: ClickHouseClient | None = None


def get_client() -> ClickHouseClient:
    global _client
    if _client is None:
        _client = ClickHouseClient()
    return _client
