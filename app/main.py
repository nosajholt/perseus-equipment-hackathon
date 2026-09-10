"""Perseus BI Dashboard - FastAPI backend.

Serves the Executive Overview dashboard and its read-only JSON API.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, metrics

STATIC_DIR = Path(__file__).resolve().parent / "static"

log = logging.getLogger("perseus")


def _warm_cache() -> None:
    """Pre-compute the two presets the UI opens with.

    A cold all-time aggregation is several seconds because it touches every one
    of the 351k invoice detail lines. Doing it at startup keeps that cost off the
    first request instead of making the user wait for it.
    """
    try:
        bounds = metrics.meta()
        lo, hi = bounds["min_date"], bounds["max_date"]
        last_12m = date.fromisoformat(hi).replace(year=date.fromisoformat(hi).year - 1).isoformat()

        for start, end in ((last_12m, hi), (lo, hi)):
            metrics.kpis(start, end)
            for grain in ("month", "quarter", "year"):
                metrics.trend(start, end, grain)
            metrics.mix(start, end)
            metrics.data_quality(start, end)
            metrics.top_customers(start, end, 10)
            metrics.top_salespeople(start, end, 10)
            metrics.service(start, end)
            metrics.rental(start, end)
        log.info("cache warmed: %s", db.cache_stats())
    except Exception:  # a warming failure must never stop the server
        log.exception("cache warming failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_warm_cache, name="warm-cache", daemon=True).start()
    yield


app = FastAPI(title="Perseus BI Dashboard", version="1.0.0", lifespan=lifespan)


def _range(start: str | None, end: str | None) -> tuple[str, str]:
    """Validate a date range and clamp it to the span the data actually covers."""
    bounds = metrics.meta()
    lo, hi = bounds["min_date"], bounds["max_date"]

    try:
        s = date.fromisoformat(start) if start else date.fromisoformat(lo)
        e = date.fromisoformat(end) if end else date.fromisoformat(hi)
    except ValueError:
        raise HTTPException(422, "start and end must be ISO dates (YYYY-MM-DD)")

    if s > e:
        raise HTTPException(422, "start must not be after end")

    return s.isoformat(), e.isoformat()


# ------------------------------------------------------------------- metadata


@app.get("/api/meta")
def api_meta() -> dict:
    return metrics.meta()


@app.get("/api/health")
def api_health() -> dict:
    return {
        "status": "ok",
        "database": db.DB_PATH.name,
        "database_bytes": db.DB_PATH.stat().st_size,
        "tables": len(list(db.table_names())),
        "cache": db.cache_stats(),
    }


# ---------------------------------------------------------------- core panels


@app.get("/api/kpis")
def api_kpis(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.kpis(s, e)


@app.get("/api/trend")
def api_trend(
    start: str | None = None,
    end: str | None = None,
    grain: str = Query("month", pattern="^(month|quarter|year)$"),
) -> dict:
    s, e = _range(start, end)
    return metrics.trend(s, e, grain)


@app.get("/api/mix")
def api_mix(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.mix(s, e)


# -------------------------------------------------------------- detail panels


@app.get("/api/top-customers")
def api_top_customers(
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(10, ge=1, le=100),
) -> dict:
    s, e = _range(start, end)
    return metrics.top_customers(s, e, limit)


@app.get("/api/top-salespeople")
def api_top_salespeople(
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(10, ge=1, le=100),
) -> dict:
    s, e = _range(start, end)
    return metrics.top_salespeople(s, e, limit)


@app.get("/api/service")
def api_service(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.service(s, e)


@app.get("/api/rental")
def api_rental(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.rental(s, e)


@app.get("/api/data-quality")
def api_data_quality(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.data_quality(s, e)


# ------------------------------------------------------------- table paging


GRAIN = Query("month", pattern="^(month|quarter|year)$")
PAGE = Query(1, ge=1)
PAGE_SIZE = Query(25, ge=5, le=metrics.MAX_PAGE_SIZE)
DIRECTION = Query("desc", pattern="^(asc|desc)$")


# --------------------------------------------------------------- units / sales


@app.get("/api/units/summary")
def api_units_summary(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.units_summary(s, e)


@app.get("/api/units/charts")
def api_units_charts(
    start: str | None = None, end: str | None = None, grain: str = GRAIN
) -> dict:
    s, e = _range(start, end)
    return metrics.units_charts(s, e, grain)


@app.get("/api/units/detail")
def api_units_detail(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    category: str | None = None,
    condition: str | None = None,
    make: str | None = None,
    salesperson: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.units_detail(
        s, e, page, page_size, sort, dir, category, condition, make, salesperson, search
    )


@app.get("/api/units/inventory")
def api_units_inventory(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    bucket: str | None = None,
    category: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.units_inventory(
        s, e, page, page_size, sort, dir, bucket, category, search
    )


# ---------------------------------------------------------------------- parts


@app.get("/api/parts/summary")
def api_parts_summary(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.parts_summary(s, e)


@app.get("/api/parts/charts")
def api_parts_charts(
    start: str | None = None, end: str | None = None, grain: str = GRAIN
) -> dict:
    s, e = _range(start, end)
    return metrics.parts_charts(s, e, grain)


@app.get("/api/parts/detail")
def api_parts_detail(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    part_group: str | None = None,
    manufacturer: str | None = None,
    stocking_class: str | None = None,
    band: str | None = None,
    bin: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.parts_detail(
        s,
        e,
        page,
        page_size,
        sort,
        dir,
        part_group,
        manufacturer,
        stocking_class,
        band,
        bin,
        search,
    )


@app.get("/api/parts/stock")
def api_parts_stock(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    bucket: str | None = None,
    part_group: str | None = None,
    stocking_class: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.parts_stock(
        s, e, page, page_size, sort, dir, bucket, part_group, stocking_class, search
    )


# -------------------------------------------------------------------- service


@app.get("/api/service/summary")
def api_service_summary(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.service_summary(s, e)


@app.get("/api/service/charts")
def api_service_charts(
    start: str | None = None, end: str | None = None, grain: str = GRAIN
) -> dict:
    s, e = _range(start, end)
    return metrics.service_charts(s, e, grain)


@app.get("/api/service/detail")
def api_service_detail(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    bill_as: str | None = None,
    wo_status: str | None = None,
    tech: str | None = None,
    segment_status: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.service_detail(
        s, e, page, page_size, sort, dir, bill_as, wo_status, tech, segment_status, search
    )


@app.get("/api/service/wip")
def api_service_wip(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    bucket: str | None = None,
    wo_status: str | None = None,
    doc_status: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.service_wip(
        s, e, page, page_size, sort, dir, bucket, wo_status, doc_status, search
    )


# --------------------------------------------------------------------- rental


@app.get("/api/rental/summary")
def api_rental_summary(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.rental_summary(s, e)


@app.get("/api/rental/charts")
def api_rental_charts(
    start: str | None = None, end: str | None = None, grain: str = GRAIN
) -> dict:
    s, e = _range(start, end)
    return metrics.rental_charts(s, e, grain)


@app.get("/api/rental/detail")
def api_rental_detail(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    rental_group: str | None = None,
    duration_unit: str | None = None,
    contract_status: str | None = None,
    stock_no: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.rental_detail(
        s,
        e,
        page,
        page_size,
        sort,
        dir,
        rental_group,
        duration_unit,
        contract_status,
        stock_no,
        search,
    )


@app.get("/api/rental/fleet")
def api_rental_fleet(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    bucket: str | None = None,
    rental_group: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.rental_fleet(
        s, e, page, page_size, sort, dir, bucket, rental_group, search
    )


# ------------------------------------------------------------------ customers


@app.get("/api/customers/summary")
def api_customers_summary(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.customers_summary(s, e)


@app.get("/api/customers/charts")
def api_customers_charts(start: str | None = None, end: str | None = None) -> dict:
    s, e = _range(start, end)
    return metrics.customers_charts(s, e)


@app.get("/api/customers/detail")
def api_customers_detail(
    start: str | None = None,
    end: str | None = None,
    page: int = PAGE,
    page_size: int = PAGE_SIZE,
    sort: str | None = None,
    dir: str = DIRECTION,
    segment: str | None = None,
    state: str | None = None,
    customer_class: str | None = None,
    band: str | None = None,
    search: str | None = None,
) -> dict:
    s, e = _range(start, end)
    return metrics.customers_detail(
        s, e, page, page_size, sort, dir, segment, state, customer_class, band, search
    )


# ------------------------------------------------------------------- frontend


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
