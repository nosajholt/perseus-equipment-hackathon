"""Semantic layer for the Perseus BI Dashboard.

Every number the dashboard shows is defined here, once, so the UI never invents
its own business rules.

Three definitions carry most of the weight:

1. Only `finalized` and `archived` invoices count as booked business. Voided
   invoices alone total ~$15M and would distort every revenue figure.

2. `InvoiceDetail.ItemType` maps 1:1 onto the detail tables, so department
   attribution is exact rather than heuristic.

3. Trade-ins are NOT negative revenue. On an invoice a trade-in reduces the
   amount billed, but economically it is inventory acquired, not revenue lost.
   Netting trade-ins into unit sales would report ~$95M revenue against ~$95M
   cost and show Sales running at roughly zero margin, which is wrong. They are
   reported separately as a trade-in allowance instead.

Two traps in this schema are worth naming, because both silently corrupt revenue
if missed:

`IsActive` is 0 for every archived and voided invoice, so the seemingly natural
filter `Status IN ('finalized','archived') AND IsActive = 1` quietly collapses to
finalized-only and discards $20.1M of real archived business. Status alone is the
correct filter.

`QU` lines are quoted items and appear only on archived or quote documents, never
on a finalized invoice, so they are excluded from revenue. That also removes a
single joke record - a $10,000,000 "Jar of Gypsy Tears" line entered 2023-03-02 -
which by itself accounts for the entire apparent 2023 revenue spike.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from . import db

BOOKED_STATUSES = ("finalized", "archived")

# ItemType -> reporting department. Verified 1:1 against the detail tables:
# SaleUnit=5747=UN, RentalUnit=22995=RU, InvoiceSegment=31134=SL.
DEPARTMENT_BY_ITEM_TYPE = {
    "UN": "Sales",  # unit sales
    "PA": "Parts",  # parts lines
    "SL": "Service",  # labor segments
    "RU": "Rental",  # rental units
    "MC": "Other",  # miscellaneous charges
    "RE": "Other",  # rental returns, always zero value
    "TR": "Trade-In",  # inventory acquired, not revenue
    "QU": "Quote Line",  # quoted but never booked
}

DEPARTMENT_ORDER = ["Sales", "Parts", "Service", "Rental", "Other"]

TRADE_IN = "Trade-In"
QUOTE_LINE = "Quote Line"

# Buckets that exist so their value can be reported, but which must never land
# in revenue.
NON_REVENUE_DEPARTMENTS = (TRADE_IN, QUOTE_LINE)

# Only these departments have a real cost basis in the data, so they are the
# only ones a gross margin can honestly be computed for.
#   Sales   -> SaleUnit.InvoiceCost
#   Parts   -> SalePart.AvgCost
# Service has no cost column and AppUser.HourlyRate is 0 for all 48 users.
# Rental's RentalUnit.DepreciationAmt is 0 for all 22,995 rows.
COSTED_DEPARTMENTS = ("Sales", "Parts")

# The same two partitions expressed as item types, derived from the mapping
# above rather than restated so they cannot drift out of step with it. Used
# where a filter should hit InvoiceDetail's ItemType index directly instead of
# forcing SQLite to evaluate the department CASE over every detail row.
NON_REVENUE_ITEM_TYPES = tuple(
    item for item, dept in DEPARTMENT_BY_ITEM_TYPE.items() if dept in NON_REVENUE_DEPARTMENTS
)
COSTED_ITEM_TYPES = tuple(
    item for item, dept in DEPARTMENT_BY_ITEM_TYPE.items() if dept in COSTED_DEPARTMENTS
)


# --------------------------------------------------------------- SQL building


def _department_case(alias: str = "d") -> str:
    whens = " ".join(
        f"WHEN '{item}' THEN '{dept}'" for item, dept in DEPARTMENT_BY_ITEM_TYPE.items()
    )
    return f"CASE {alias}.ItemType {whens} ELSE 'Other' END"


def _cost_case(alias: str = "d") -> str:
    """Per-line cost, pulled from whichever detail table owns the line."""
    return f"""
        CASE {alias}.ItemType
            WHEN 'UN' THEN COALESCE(
                (SELECT su.InvoiceCost * su.Qty FROM SaleUnit su WHERE su.ItemId = {alias}.ItemId), 0)
            WHEN 'PA' THEN COALESCE(
                (SELECT sp.AvgCost * sp.Qty FROM SalePart sp WHERE sp.ItemId = {alias}.ItemId), 0)
            ELSE 0
        END
    """


def _lines_cte() -> str:
    """Line-level CTE that every revenue metric is built on.

    Status alone defines booked business. `IsActive` is deliberately not filtered
    here: it is 0 on every archived invoice, so adding it would silently discard
    all archived revenue.

    ActivityDate is compared as a string rather than wrapped in date() so the
    existing indexes stay usable.
    """
    return f"""
    WITH lines AS (
        SELECT
            h.InvoiceDocId,
            h.ActivityDate,
            h.CustomerId,
            h.CustomerName,
            h.CustomerNo,
            h.SalesPersonName,
            h.InvoiceType,
            d.ItemId,
            d.ItemType,
            {_department_case()} AS department,
            d.NetExt AS revenue,
            {_cost_case()} AS cost
        FROM InvoiceHeader h
        JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start
          AND h.ActivityDate <= :end
    )
    """


def _period_expr(grain: str, column: str = "ActivityDate") -> str:
    if grain == "year":
        return f"substr({column}, 1, 4)"
    if grain == "quarter":
        return (
            f"substr({column}, 1, 4) || '-Q' || "
            f"((CAST(substr({column}, 6, 2) AS INTEGER) + 2) / 3)"
        )
    return f"substr({column}, 1, 7)"


def _bounds(start: str, end: str) -> dict[str, str]:
    """Widen a date range to cover the whole end day, for string comparison."""
    return {"start": f"{start} 00:00:00", "end": f"{end} 23:59:59"}


# ------------------------------------------------------------ date arithmetic


def _shift_years(iso: str, years: int) -> str:
    d = date.fromisoformat(iso)
    try:
        return d.replace(year=d.year + years).isoformat()
    except ValueError:  # Feb 29 -> Feb 28
        return d.replace(year=d.year + years, day=28).isoformat()


def _prior_period(start: str, end: str) -> tuple[str, str]:
    """The equally sized window immediately before `start`."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    span = (e - s).days
    prior_end = date.fromordinal(s.toordinal() - 1)
    prior_start = date.fromordinal(prior_end.toordinal() - span)
    return prior_start.isoformat(), prior_end.isoformat()


def _prior_year(start: str, end: str) -> tuple[str, str]:
    return _shift_years(start, -1), _shift_years(end, -1)


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


# ------------------------------------------------------------------ metadata


def meta() -> dict[str, Any]:
    """Date bounds, dimension values and defaults for the filter bar."""

    def build() -> dict[str, Any]:
        bounds = (
            db.query_one(
                f"""
                SELECT MIN(date(ActivityDate)) AS min_date,
                       MAX(date(ActivityDate)) AS max_date,
                       COUNT(*)                AS invoices
                FROM InvoiceHeader
                WHERE Status IN {BOOKED_STATUSES}
                """
            )
            or {}
        )

        # Invoices that actually carry revenue lines. Around 29.7k booked headers
        # have no detail rows at all, but 29.7k of those are $0 shells worth
        # $0.4M in total, so they are immaterial as well as unreportable.
        with_revenue = (
            db.query_one(
                f"""
                {_lines_cte()}
                SELECT COUNT(DISTINCT InvoiceDocId) AS n
                FROM lines WHERE department NOT IN {NON_REVENUE_DEPARTMENTS}
                """,
                _bounds("2000-01-01", "2100-12-31"),
            )
            or {}
        )

        location = (
            db.query_one(
                "SELECT DisplayText, CompanyName, City, State FROM SettingsLocation LIMIT 1"
            )
            or {}
        )

        salespeople = db.query(
            f"""
            SELECT SalesPersonName AS name, COUNT(*) AS invoices
            FROM InvoiceHeader
            WHERE Status IN {BOOKED_STATUSES}
              AND TRIM(SalesPersonName) <> ''
            GROUP BY SalesPersonName
            ORDER BY invoices DESC
            """
        )

        return {
            "min_date": bounds.get("min_date"),
            "max_date": bounds.get("max_date"),
            "booked_invoices": bounds.get("invoices"),
            "revenue_invoices": with_revenue.get("n"),
            "location": location,
            "departments": DEPARTMENT_ORDER,
            "costed_departments": list(COSTED_DEPARTMENTS),
            "salespeople": salespeople,
            "booked_statuses": list(BOOKED_STATUSES),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    return db.cached("meta", None, build)


# ---------------------------------------------------------------------- KPIs


_TOTALS_SQL = f"""
{_lines_cte()}
SELECT
    SUM(CASE WHEN department NOT IN {NON_REVENUE_DEPARTMENTS} THEN revenue ELSE 0 END) AS revenue,
    SUM(CASE WHEN department IN {COSTED_DEPARTMENTS} THEN revenue ELSE 0 END)    AS costed_revenue,
    SUM(CASE WHEN department IN {COSTED_DEPARTMENTS} THEN cost ELSE 0 END)       AS costed_cost,
    SUM(CASE WHEN department = '{TRADE_IN}' THEN -revenue ELSE 0 END)            AS trade_in_allowance,
    SUM(CASE WHEN department = '{QUOTE_LINE}' THEN revenue ELSE 0 END)           AS quote_lines_excluded,
    COUNT(DISTINCT CASE WHEN department NOT IN {NON_REVENUE_DEPARTMENTS} THEN InvoiceDocId END) AS invoices,
    SUM(CASE WHEN ItemType = 'UN' THEN 1 ELSE 0 END)                            AS units_sold,
    COALESCE(SUM(CASE WHEN ItemType = 'SL' THEN
        (SELECT s.ActualHrs FROM InvoiceSegment s WHERE s.ItemId = lines.ItemId) END), 0) AS labor_hours
FROM lines
"""


def _totals(start: str, end: str) -> dict[str, Any]:
    row = db.query_one(_TOTALS_SQL, _bounds(start, end)) or {}
    revenue = row.get("revenue") or 0.0
    costed_revenue = row.get("costed_revenue") or 0.0
    costed_cost = row.get("costed_cost") or 0.0
    invoices = row.get("invoices") or 0

    gross_profit = costed_revenue - costed_cost
    return {
        "revenue": revenue,
        "costed_revenue": costed_revenue,
        "gross_profit": gross_profit,
        "gross_margin_pct": (gross_profit / costed_revenue * 100) if costed_revenue else None,
        "trade_in_allowance": row.get("trade_in_allowance") or 0.0,
        "quote_lines_excluded": row.get("quote_lines_excluded") or 0.0,
        "invoices": invoices,
        "avg_ticket": (revenue / invoices) if invoices else None,
        "units_sold": row.get("units_sold") or 0,
        "labor_hours": row.get("labor_hours") or 0.0,
    }


KPI_FIELDS = (
    "revenue",
    "gross_profit",
    "gross_margin_pct",
    "invoices",
    "avg_ticket",
    "units_sold",
    "labor_hours",
    "trade_in_allowance",
)


def kpis(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        current = _totals(start, end)
        pp_start, pp_end = _prior_period(start, end)
        py_start, py_end = _prior_year(start, end)
        prior_period = _totals(pp_start, pp_end)
        prior_year = _totals(py_start, py_end)

        monthly = trend(start, end, "month")["periods"]
        sparklines: dict[str, list[float]] = {}
        for field in KPI_FIELDS:
            series = [p["totals"].get(field) for p in monthly]
            sparklines[field] = [round(v, 2) if v is not None else None for v in series]

        out: dict[str, Any] = {
            "sparklines": sparklines,
            "windows": {
                "current": {"start": start, "end": end},
                "prior_period": {"start": pp_start, "end": pp_end},
                "prior_year": {"start": py_start, "end": py_end},
            },
        }
        for field in KPI_FIELDS:
            out[field] = {
                "value": current[field],
                "prior_period": prior_period[field],
                "prior_year": prior_year[field],
                "prior_period_pct": _pct_change(current[field], prior_period[field]),
                "prior_year_pct": _pct_change(current[field], prior_year[field]),
            }
        return out

    return db.cached("kpis", [start, end], build)


# --------------------------------------------------------------------- trend


def _period_end(period: str, grain: str) -> date:
    """Last calendar day covered by a period label."""
    if grain == "year":
        return date(int(period), 12, 31)
    if grain == "quarter":
        year, q = period.split("-Q")
        month = int(q) * 3
        return _month_end(int(year), month)
    year, month = period.split("-")
    return _month_end(int(year), int(month))


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date.fromordinal(date(year, month + 1, 1).toordinal() - 1)


def trend(start: str, end: str, grain: str = "month") -> dict[str, Any]:
    grain = grain if grain in ("month", "quarter", "year") else "month"

    def build() -> dict[str, Any]:
        period = _period_expr(grain)
        rows = db.query(
            f"""
            {_lines_cte()}
            SELECT {period}                       AS period,
                   department,
                   SUM(revenue)                   AS revenue,
                   SUM(cost)                      AS cost,
                   COUNT(*)                       AS lines,
                   COUNT(DISTINCT InvoiceDocId)   AS invoices,
                   SUM(CASE WHEN ItemType = 'UN' THEN 1 ELSE 0 END) AS units_sold,
                   COALESCE(SUM(CASE WHEN ItemType = 'SL' THEN
                       (SELECT s.ActualHrs FROM InvoiceSegment s WHERE s.ItemId = lines.ItemId) END), 0) AS labor_hours
            FROM lines
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            _bounds(start, end),
        )

        buckets: dict[str, dict[str, Any]] = {}
        for r in rows:
            b = buckets.setdefault(
                r["period"],
                {
                    "period": r["period"],
                    "revenue": {d: 0.0 for d in DEPARTMENT_ORDER},
                    "cost": {d: 0.0 for d in DEPARTMENT_ORDER},
                    "gross_profit": {d: 0.0 for d in DEPARTMENT_ORDER},
                    "trade_in_allowance": 0.0,
                    "quote_lines_excluded": 0.0,
                    "invoices": 0,
                    "units_sold": 0,
                    "labor_hours": 0.0,
                },
            )
            dept = r["department"]
            if dept == TRADE_IN:
                b["trade_in_allowance"] += -(r["revenue"] or 0.0)
                continue
            if dept == QUOTE_LINE:
                b["quote_lines_excluded"] += r["revenue"] or 0.0
                continue

            b["revenue"][dept] = r["revenue"] or 0.0
            b["cost"][dept] = r["cost"] or 0.0
            # Only costed departments get a gross profit; the rest stay at zero
            # rather than being silently reported at 100% margin.
            b["gross_profit"][dept] = (
                (r["revenue"] or 0.0) - (r["cost"] or 0.0) if dept in COSTED_DEPARTMENTS else 0.0
            )
            b["units_sold"] += r["units_sold"] or 0
            b["labor_hours"] += r["labor_hours"] or 0.0

        # Invoice counts must be counted per period, not summed across departments.
        for r in db.query(
            f"""
            {_lines_cte()}
            SELECT {period} AS period, COUNT(DISTINCT InvoiceDocId) AS invoices
            FROM lines WHERE department NOT IN {NON_REVENUE_DEPARTMENTS} GROUP BY 1
            """,
            _bounds(start, end),
        ):
            if r["period"] in buckets:
                buckets[r["period"]]["invoices"] = r["invoices"]

        data_max = date.fromisoformat(meta()["max_date"])
        window_end = min(date.fromisoformat(end), data_max)

        periods = []
        for key in sorted(buckets):
            b = buckets[key]
            revenue = sum(b["revenue"].values())
            costed_revenue = sum(b["revenue"][d] for d in COSTED_DEPARTMENTS)
            costed_cost = sum(b["cost"][d] for d in COSTED_DEPARTMENTS)
            gross_profit = costed_revenue - costed_cost

            b["totals"] = {
                "revenue": revenue,
                "costed_revenue": costed_revenue,
                "gross_profit": gross_profit,
                "gross_margin_pct": (gross_profit / costed_revenue * 100) if costed_revenue else None,
                "trade_in_allowance": b["trade_in_allowance"],
                "quote_lines_excluded": b["quote_lines_excluded"],
                "invoices": b["invoices"],
                "avg_ticket": (revenue / b["invoices"]) if b["invoices"] else None,
                "units_sold": b["units_sold"],
                "labor_hours": b["labor_hours"],
            }
            # A period is partial when the data stops before the period does, so
            # the UI can hatch it instead of showing a phantom collapse.
            b["partial"] = _period_end(key, grain) > window_end
            periods.append(b)

        return {"grain": grain, "periods": periods}

    return db.cached("trend", [start, end, grain], build)


# ----------------------------------------------------------------------- mix


def mix(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        rows = db.query(
            f"""
            {_lines_cte()}
            SELECT department,
                   SUM(revenue)                 AS revenue,
                   SUM(cost)                    AS cost,
                   COUNT(*)                     AS lines,
                   COUNT(DISTINCT InvoiceDocId) AS invoices
            FROM lines
            GROUP BY department
            """,
            _bounds(start, end),
        )
        by_dept = {r["department"]: r for r in rows}

        out = []
        for dept in DEPARTMENT_ORDER:
            r = by_dept.get(dept)
            revenue = (r["revenue"] if r else 0.0) or 0.0
            cost = (r["cost"] if r else 0.0) or 0.0
            costed = dept in COSTED_DEPARTMENTS
            out.append(
                {
                    "department": dept,
                    "revenue": revenue,
                    "cost": cost if costed else None,
                    "gross_profit": (revenue - cost) if costed else None,
                    "margin_pct": ((revenue - cost) / revenue * 100) if costed and revenue else None,
                    "has_cost_basis": costed,
                    "lines": (r["lines"] if r else 0) or 0,
                    "invoices": (r["invoices"] if r else 0) or 0,
                }
            )

        trade = by_dept.get(TRADE_IN)
        quotes = by_dept.get(QUOTE_LINE)
        return {
            "departments": out,
            "trade_in_allowance": -((trade["revenue"] if trade else 0.0) or 0.0),
            "quote_lines_excluded": (quotes["revenue"] if quotes else 0.0) or 0.0,
        }

    return db.cached("mix", [start, end], build)


# ------------------------------------------------------------------ rankings


def top_customers(start: str, end: str, limit: int = 10) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        rows = db.query(
            f"""
            {_lines_cte()}
            SELECT CustomerId                    AS customer_id,
                   MAX(CustomerNo)               AS customer_no,
                   MAX(CustomerName)             AS customer_name,
                   SUM(revenue)                  AS revenue,
                   SUM(CASE WHEN department IN {COSTED_DEPARTMENTS} THEN revenue - cost ELSE 0 END) AS gross_profit,
                   COUNT(DISTINCT InvoiceDocId)  AS invoices
            FROM lines
            WHERE department NOT IN {NON_REVENUE_DEPARTMENTS}
            GROUP BY CustomerId
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            {**_bounds(start, end), "limit": limit},
        )
        return {"customers": rows}

    return db.cached("top_customers", [start, end, limit], build)


def top_salespeople(start: str, end: str, limit: int = 10) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        rows = db.query(
            f"""
            {_lines_cte()}
            SELECT TRIM(SalesPersonName)         AS name,
                   SUM(revenue)                  AS revenue,
                   SUM(CASE WHEN department IN {COSTED_DEPARTMENTS} THEN revenue - cost ELSE 0 END) AS gross_profit,
                   COUNT(DISTINCT InvoiceDocId)  AS invoices,
                   SUM(CASE WHEN ItemType = 'UN' THEN 1 ELSE 0 END) AS units_sold
            FROM lines
            WHERE department NOT IN {NON_REVENUE_DEPARTMENTS} AND TRIM(SalesPersonName) <> ''
            GROUP BY TRIM(SalesPersonName)
            ORDER BY revenue DESC
            LIMIT :limit
            """,
            {**_bounds(start, end), "limit": limit},
        )
        return {"salespeople": rows}

    return db.cached("top_salespeople", [start, end, limit], build)


# ------------------------------------------------------------------- service


def service(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)

        billed = (
            db.query_one(
                f"""
                SELECT COALESCE(SUM(s.ActualHrs), 0)          AS billed_hours,
                       COALESCE(SUM(s.FlatRateLaborHrs), 0)   AS flat_rate_hours,
                       COALESCE(SUM(s.NetExt), 0)             AS labor_revenue,
                       COUNT(*)                               AS segments,
                       COUNT(DISTINCT s.InvDocId)             AS work_orders
                FROM InvoiceSegment s
                JOIN InvoiceHeader h ON h.InvoiceDocId = s.InvDocId
                WHERE h.Status IN {BOOKED_STATUSES}
                  AND h.ActivityDate >= :start AND h.ActivityDate <= :end
                """,
                bounds,
            )
            or {}
        )

        # Clocked time is filtered on its own timestamp, since a technician's
        # hours are worked whether or not the invoice is finalized yet.
        clocked = (
            db.query_one(
                """
                SELECT COALESCE(SUM(ElapsedHours), 0) AS clock_hours,
                       COUNT(DISTINCT TechId)         AS techs,
                       COUNT(*)                       AS punches
                FROM WorkInProgress
                WHERE TimeOn >= :start AND TimeOn <= :end AND IsActive = 1
                """,
                bounds,
            )
            or {}
        )

        techs = db.query(
            """
            SELECT w.TechId                                        AS tech_id,
                   COALESCE(NULLIF(TRIM(u.FirstName || ' ' || u.LastName), ''), u.UserName,
                            'Tech ' || w.TechId)                    AS name,
                   COALESCE(SUM(w.ElapsedHours), 0)                 AS clock_hours,
                   COUNT(*)                                         AS punches
            FROM WorkInProgress w
            LEFT JOIN AppUser u ON u.AppUserId = w.TechId
            WHERE w.TimeOn >= :start AND w.TimeOn <= :end AND w.IsActive = 1
            GROUP BY w.TechId
            ORDER BY clock_hours DESC
            LIMIT 12
            """,
            bounds,
        )

        billed_hours = billed.get("billed_hours") or 0.0
        clock_hours = clocked.get("clock_hours") or 0.0
        labor_revenue = billed.get("labor_revenue") or 0.0

        return {
            "billed_hours": billed_hours,
            "flat_rate_hours": billed.get("flat_rate_hours") or 0.0,
            "labor_revenue": labor_revenue,
            "segments": billed.get("segments") or 0,
            "work_orders": billed.get("work_orders") or 0,
            "clock_hours": clock_hours,
            "techs_active": clocked.get("techs") or 0,
            "effective_rate": (labor_revenue / billed_hours) if billed_hours else None,
            # Share of clocked time that reached an invoice.
            "utilization_pct": (billed_hours / clock_hours * 100) if clock_hours else None,
            "techs": techs,
            "has_cost_basis": False,
        }

    return db.cached("service", [start, end], build)


# -------------------------------------------------------------------- rental


def rental(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)

        totals = (
            db.query_one(
                f"""
                SELECT COALESCE(SUM(ru.NetExt), 0)      AS revenue,
                       COUNT(*)                         AS rental_lines,
                       COUNT(DISTINCT ru.UnitId)        AS units_rented,
                       COUNT(DISTINCT h.InvoiceDocId)   AS invoices,
                       COALESCE(AVG(CASE
                           WHEN ru.EndDate IS NOT NULL AND ru.StartDate IS NOT NULL
                           THEN julianday(ru.EndDate) - julianday(ru.StartDate)
                       END), 0)                         AS avg_days,
                       SUM(CASE WHEN ru.ReturnDate IS NULL THEN 1 ELSE 0 END) AS missing_return
                FROM RentalUnit ru
                JOIN InvoiceDetail d ON d.ItemId = ru.ItemId
                JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
                WHERE h.Status IN {BOOKED_STATUSES}
                  AND h.ActivityDate >= :start AND h.ActivityDate <= :end
                """,
                bounds,
            )
            or {}
        )

        fleet = (
            db.query_one(
                """
                SELECT COUNT(*)                                              AS fleet_size,
                       SUM(CASE WHEN IsActive = 1 THEN 1 ELSE 0 END)          AS active_fleet
                FROM UnitBase WHERE Rental = 1
                """
            )
            or {}
        )

        contracts = (
            db.query_one(
                f"""
                SELECT COUNT(DISTINCT rc.RentalContractId) AS contracts
                FROM RentalContract rc
                JOIN InvoiceHeader h ON h.InvoiceDocId = rc.InvoiceDocId
                WHERE h.Status IN {BOOKED_STATUSES}
                  AND h.ActivityDate >= :start AND h.ActivityDate <= :end
                """,
                bounds,
            )
            or {}
        )

        top_units = db.query(
            f"""
            SELECT ru.StockNo                    AS stock_no,
                   MAX(ru.Model)                 AS model,
                   MAX(ru.Description)           AS description,
                   COALESCE(SUM(ru.NetExt), 0)   AS revenue,
                   COUNT(*)                      AS rentals
            FROM RentalUnit ru
            JOIN InvoiceDetail d ON d.ItemId = ru.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE h.Status IN {BOOKED_STATUSES}
              AND h.ActivityDate >= :start AND h.ActivityDate <= :end
            GROUP BY ru.StockNo
            ORDER BY revenue DESC
            LIMIT 8
            """,
            bounds,
        )

        units_rented = totals.get("units_rented") or 0
        rental_lines = totals.get("rental_lines") or 0
        active_fleet = fleet.get("active_fleet") or 0

        # `UnitBase.Rental` is a current snapshot, not history, so over a long
        # window more distinct units get rented than are in the fleet today.
        # Rather than report a nonsensical figure like 642%, the ratio is only
        # offered when the comparison is actually valid.
        fleet_comparable = bool(active_fleet) and units_rented <= active_fleet

        return {
            "revenue": totals.get("revenue") or 0.0,
            "rental_lines": rental_lines,
            "contracts": contracts.get("contracts") or 0,
            "units_rented": units_rented,
            "fleet_size": fleet.get("fleet_size") or 0,
            "active_fleet": active_fleet,
            "fleet_on_rent_pct": (units_rented / active_fleet * 100) if fleet_comparable else None,
            "fleet_comparable": fleet_comparable,
            "turns_per_unit": (rental_lines / units_rented) if units_rented else None,
            "avg_days": totals.get("avg_days") or 0.0,
            "missing_return": totals.get("missing_return") or 0,
            "top_units": top_units,
            "has_cost_basis": False,
        }

    return db.cached("rental", [start, end], build)


# -------------------------------------------------------------- data quality


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def data_quality(start: str, end: str) -> dict[str, Any]:
    """Caveats that change how these numbers should be read.

    Surfaced as a first-class panel rather than left for someone to trip over.
    """

    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)
        issues: list[dict[str, Any]] = []

        # 1. Sustained step change in revenue.
        #
        # The raw data appears to show a one-off 2023 spike to $30.3M, but that
        # figure comes from summing InvoiceHeader.TotalInvoice across every status
        # and includes a single $10M joke line item. Once booked-only, line-level
        # revenue is used, 2023-2025 are a sustained plateau roughly 50% above the
        # 2018-2022 baseline, which is a genuine trend rather than an anomaly.
        yearly = db.query(
            f"""
            {_lines_cte()}
            SELECT substr(ActivityDate, 1, 4) AS yr, SUM(revenue) AS revenue
            FROM lines WHERE department NOT IN {NON_REVENUE_DEPARTMENTS} GROUP BY 1 ORDER BY 1
            """,
            _bounds("2000-01-01", "2100-12-31"),
        )
        data_max = date.fromisoformat(meta()["max_date"])
        data_min = date.fromisoformat(meta()["min_date"])
        complete = [
            r
            for r in yearly
            if int(r["yr"]) > data_min.year and int(r["yr"]) < data_max.year
        ]
        med = _median([r["revenue"] or 0.0 for r in complete]) or 0.0

        if len(complete) >= 5:
            recent = complete[-3:]
            baseline = complete[:-3]
            recent_avg = sum(r["revenue"] or 0.0 for r in recent) / len(recent)
            base_avg = (
                sum(r["revenue"] or 0.0 for r in baseline) / len(baseline) if baseline else 0.0
            )
            if base_avg and recent_avg / base_avg > 1.25:
                issues.append(
                    {
                        "code": "revenue_step_change",
                        "title": (
                            f"Revenue stepped up {((recent_avg / base_avg) - 1) * 100:.0f}% from "
                            f"{recent[0]['yr']} and has held there"
                        ),
                        "detail": (
                            f"{recent[0]['yr']}-{recent[-1]['yr']} averaged ${recent_avg:,.0f} against "
                            f"${base_avg:,.0f} for {baseline[0]['yr']}-{baseline[-1]['yr']}. This is a "
                            f"sustained plateau, not a single good year. Note the raw data appears to "
                            f"show a much larger 2023 spike; that artifact comes from summing all "
                            f"invoice statuses and a $10M joke line item, both excluded here."
                        ),
                        "impact": "medium",
                    }
                )

        # 2. Quote lines held out of revenue, including the joke record.
        quote_lines = (
            db.query_one(
                f"""
                {_lines_cte()}
                SELECT COUNT(*) AS n, COALESCE(SUM(revenue), 0) AS amount
                FROM lines WHERE department = '{QUOTE_LINE}'
                """,
                bounds,
            )
            or {}
        )
        n_quotes = quote_lines.get("n") or 0
        if n_quotes > 0:
            amount = quote_lines.get("amount") or 0.0
            issues.append(
                {
                    "code": "quote_lines_excluded",
                    "title": (
                        f"{n_quotes:,} quote line{'' if n_quotes == 1 else 's'} "
                        f"(${amount:,.0f}) excluded from revenue"
                    ),
                    "detail": (
                        "Quoted lines appear only on archived or quote documents, never on a "
                        "finalized invoice, so they are not booked revenue. This is also what "
                        "removes a single $10,000,000 'Jar of Gypsy Tears' line entered "
                        "2023-03-02, which otherwise accounts for the entire apparent 2023 "
                        "revenue spike."
                    ),
                    # Only material when the excluded value is large enough to move
                    # the reported figures.
                    "impact": "high" if amount > 1_000_000 else "low",
                }
            )

        # 3. Partial trailing period.
        if date.fromisoformat(end) >= data_max:
            issues.append(
                {
                    "code": "partial_period",
                    "title": f"Data ends {data_max.isoformat()}, so the final period is incomplete",
                    "detail": (
                        "The most recent month, quarter and year are partial. They are drawn "
                        "hatched in the trend chart so a short period does not read as a decline."
                    ),
                    "impact": "medium",
                }
            )

        # 4. Lines with no cost, which inflate margin.
        zero_cost = (
            db.query_one(
                f"""
                {_lines_cte()}
                SELECT
                    SUM(CASE WHEN ItemType = 'UN' AND cost = 0 THEN 1 ELSE 0 END) AS zero_cost_units,
                    SUM(CASE WHEN ItemType = 'PA' AND cost = 0 THEN 1 ELSE 0 END) AS zero_cost_parts,
                    SUM(CASE WHEN ItemType = 'UN' AND cost = 0 THEN revenue ELSE 0 END) AS zero_cost_unit_rev,
                    SUM(CASE WHEN ItemType = 'PA' AND cost = 0 THEN revenue ELSE 0 END) AS zero_cost_part_rev
                FROM lines
                """,
                bounds,
            )
            or {}
        )
        zc_units = zero_cost.get("zero_cost_units") or 0
        zc_parts = zero_cost.get("zero_cost_parts") or 0
        if zc_units or zc_parts:
            zc_rev = (zero_cost.get("zero_cost_unit_rev") or 0) + (
                zero_cost.get("zero_cost_part_rev") or 0
            )
            issues.append(
                {
                    "code": "zero_cost_lines",
                    "title": f"{zc_units + zc_parts:,} costed lines carry zero cost",
                    "detail": (
                        f"{zc_units:,} unit-sale and {zc_parts:,} parts lines have no cost recorded, "
                        f"covering ${zc_rev:,.0f} of revenue. Those lines report as 100% margin and "
                        f"pull the blended gross margin upward."
                    ),
                    "impact": "medium",
                }
            )

        # 5. Departments with no cost basis at all.
        uncosted = (
            db.query_one(
                f"""
                {_lines_cte()}
                SELECT SUM(CASE WHEN department = 'Service' THEN revenue ELSE 0 END) AS service_rev,
                       SUM(CASE WHEN department = 'Rental'  THEN revenue ELSE 0 END) AS rental_rev
                FROM lines
                """,
                bounds,
            )
            or {}
        )
        issues.append(
            {
                "code": "no_cost_basis",
                "title": "Service and Rental have no cost basis in the data",
                "detail": (
                    f"Service labor (${uncosted.get('service_rev') or 0:,.0f}) and Rental "
                    f"(${uncosted.get('rental_rev') or 0:,.0f}) are shown as revenue only. "
                    f"AppUser.HourlyRate is 0 for all 48 users and RentalUnit.DepreciationAmt is 0 "
                    f"for all 22,995 rows, so no true margin can be computed. Gross margin is "
                    f"therefore reported over Units and Parts only."
                ),
                "impact": "high",
            }
        )

        # 6. Trade-in treatment, since it is the least obvious modelling choice.
        trade = (
            db.query_one(
                f"""
                {_lines_cte()}
                SELECT SUM(CASE WHEN department = '{TRADE_IN}' THEN -revenue ELSE 0 END) AS trade_in
                FROM lines
                """,
                bounds,
            )
            or {}
        )
        if (trade.get("trade_in") or 0) > 0:
            issues.append(
                {
                    "code": "trade_in_treatment",
                    "title": f"${trade['trade_in']:,.0f} of trade-ins are excluded from revenue",
                    "detail": (
                        "Trade-ins reduce the amount billed but are inventory acquired, not revenue "
                        "lost, so they are tracked separately. Netting them into unit sales would "
                        "show Sales at roughly zero margin."
                    ),
                    "impact": "low",
                }
            )

        # 7. Invoices deliberately excluded.
        excluded = (
            db.query_one(
                """
                SELECT Status, COUNT(*) AS n, COALESCE(SUM(TotalInvoice), 0) AS amount
                FROM InvoiceHeader
                WHERE Status NOT IN ('finalized', 'archived')
                  AND ActivityDate >= :start AND ActivityDate <= :end
                GROUP BY Status ORDER BY amount DESC LIMIT 1
                """,
                bounds,
            )
            or {}
        )
        totals_excluded = (
            db.query_one(
                """
                SELECT COUNT(*) AS n, COALESCE(SUM(TotalInvoice), 0) AS amount
                FROM InvoiceHeader
                WHERE Status NOT IN ('finalized', 'archived')
                  AND ActivityDate >= :start AND ActivityDate <= :end
                """,
                bounds,
            )
            or {}
        )
        if (totals_excluded.get("n") or 0) > 0:
            issues.append(
                {
                    "code": "excluded_invoices",
                    "title": (
                        f"{totals_excluded['n']:,} non-booked invoices "
                        f"(${totals_excluded['amount']:,.0f}) are excluded"
                    ),
                    "detail": (
                        f"Only finalized and archived invoices count as booked business. The largest "
                        f"excluded group is '{excluded.get('Status', 'n/a')}'. Voided invoices alone "
                        f"total roughly $15M across the full history."
                    ),
                    "impact": "low",
                }
            )

        # 8. Booked invoices carrying no line items.
        empty = (
            db.query_one(
                f"""
                SELECT COUNT(*) AS n, COALESCE(SUM(h.TotalInvoice), 0) AS amount,
                       SUM(CASE WHEN h.TotalInvoice = 0 THEN 1 ELSE 0 END) AS zero_value
                FROM InvoiceHeader h
                LEFT JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId
                WHERE h.Status IN {BOOKED_STATUSES} AND d.ItemId IS NULL
                  AND h.ActivityDate >= :start AND h.ActivityDate <= :end
                """,
                bounds,
            )
            or {}
        )
        if (empty.get("n") or 0) > 0:
            issues.append(
                {
                    "code": "empty_invoices",
                    "title": f"{empty['n']:,} booked invoices have no line items",
                    "detail": (
                        f"{empty.get('zero_value') or 0:,} of them are $0 shells and the whole group "
                        f"totals only ${empty.get('amount') or 0:,.0f}, so they are immaterial. They "
                        f"are excluded from invoice counts and average ticket, which is why those "
                        f"counts sit below the total booked invoice count."
                    ),
                    "impact": "low",
                }
            )

        # 9. Rental lines that never recorded a return.
        rental_gaps = (
            db.query_one(
                f"""
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN ru.ReturnDate IS NULL THEN 1 ELSE 0 END) AS missing_return
                FROM RentalUnit ru
                JOIN InvoiceDetail d ON d.ItemId = ru.ItemId
                JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
                WHERE h.Status IN {BOOKED_STATUSES}
                  AND h.ActivityDate >= :start AND h.ActivityDate <= :end
                """,
                bounds,
            )
            or {}
        )
        missing = rental_gaps.get("missing_return") or 0
        total_rental = rental_gaps.get("n") or 0
        if missing:
            issues.append(
                {
                    "code": "rental_returns",
                    "title": f"{missing:,} of {total_rental:,} rental lines have no return date",
                    "detail": (
                        "Rental duration is derived from StartDate, EndDate and DurationQty rather "
                        "than ReturnDate, which is unpopulated on most rows."
                    ),
                    "impact": "medium",
                }
            )

        order = {"high": 0, "medium": 1, "low": 2}
        issues.sort(key=lambda i: order.get(i["impact"], 3))

        return {
            "issues": issues,
            "yearly_revenue": yearly,
            "median_year_revenue": med,
            "data_max": meta()["max_date"],
        }

    return db.cached("data_quality", [start, end], build)


# --------------------------------------------------------------- paged tables


MAX_PAGE_SIZE = 200


def _paged(
    name: str,
    body: str,
    sort_map: dict[str, str],
    default_sort: str,
    params: dict[str, Any],
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    transform: Callable[[list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    """Sort and page a detail query.

    The sort key is resolved through `sort_map` instead of being interpolated, so
    a request can never reach SQL through it.

    `transform` runs inside the cached producer: the cache hands back the same
    row objects on every hit, so anything that rewrites a row has to happen once,
    before the page is stored.
    """
    page = max(1, int(page or 1))
    size = min(MAX_PAGE_SIZE, max(5, int(page_size or 25)))
    key = sort if sort in sort_map else default_sort
    order = "ASC" if str(direction).lower() == "asc" else "DESC"

    def build() -> dict[str, Any]:
        total = db.scalar(f"SELECT COUNT(*) FROM ({body})", params) or 0
        rows = db.query(
            f"SELECT * FROM ({body}) ORDER BY {sort_map[key]} {order} "
            f"LIMIT :_limit OFFSET :_offset",
            {**params, "_limit": size, "_offset": (page - 1) * size},
        )
        if transform:
            transform(rows)
        return {
            "rows": rows,
            "total": total,
            "page": page,
            "page_size": size,
            "pages": max(1, -(-total // size)),
            "sort": key,
            "dir": order.lower(),
            "columns": sorted(sort_map),
        }

    return db.cached(name, [params, key, order, page, size], build)


def _page_rows(
    rows: list[dict[str, Any]],
    sortable: tuple[str, ...],
    default_sort: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
) -> dict[str, Any]:
    """Sort and page a row set that has already been materialised.

    Used where the underlying aggregation is expensive but its result is small:
    paging in SQL would re-run the whole aggregation once per page, and once
    more for the count.
    """
    page = max(1, int(page or 1))
    size = min(MAX_PAGE_SIZE, max(5, int(page_size or 25)))
    key = sort if sort in sortable else default_sort
    reverse = str(direction).lower() != "asc"

    def value(row: dict[str, Any]) -> tuple[float, str]:
        raw = row[key]
        if isinstance(raw, str):
            return (0.0, raw.lower())
        return (float(raw), "")

    present = [r for r in rows if r.get(key) is not None]
    missing = [r for r in rows if r.get(key) is None]
    present.sort(key=value, reverse=reverse)
    # Blanks go last whichever way the column is sorted, so an empty cell never
    # occupies the top of a "highest first" view.
    ordered = present + missing

    start_at = (page - 1) * size
    return {
        "rows": ordered[start_at : start_at + size],
        "total": len(ordered),
        "page": page,
        "page_size": size,
        "pages": max(1, -(-len(ordered) // size)),
        "sort": key,
        "dir": "desc" if reverse else "asc",
        "columns": sorted(sortable),
    }


def _optional(
    conditions: list[str], params: dict[str, Any], clause: str, name: str, value: Any
) -> None:
    """Add `clause` to a WHERE list only when the caller supplied a filter value."""
    if value is None or value == "":
        return
    conditions.append(clause)
    params[name] = value


def _where(conditions: list[str]) -> str:
    return ("WHERE " + " AND ".join(conditions)) if conditions else ""


def _margin(revenue: float | None, cost: float | None) -> float | None:
    revenue = revenue or 0.0
    if not revenue:
        return None
    return (revenue - (cost or 0.0)) / revenue * 100


def _titled(value: str | None) -> str:
    """Present an upper-cased grouping key as a name.

    Free-text fields such as UnitBase.Make hold several casings of the same
    value ('Bobcat', 'BOBCAT', 'bobcat'), so they are grouped upper-cased and
    only titled for display.
    """
    return (value or "").title() or "Unspecified"


# ============================================================ INVOICE REGISTER


def _invoice_register_cte() -> str:
    """One row per booked invoice that carries revenue lines.

    This is the drill-down target for the Overview KPIs, so it admits exactly the
    invoices those KPIs count: status alone defines booked business, `IsActive` is
    deliberately not filtered because it is 0 on every archived invoice, and an
    invoice qualifies only if it has at least one line that is neither a trade-in
    nor a quote. That drops the booked headers with no detail rows at all and the
    handful whose only lines are trade-ins or quotes, which is why the register's
    total matches the Invoices KPI rather than the raw booked header count.

    Revenue is summed from the line items, not from InvoiceHeader.TotalInvoice:
    the header total carries tax and miscellaneous charges and is not restricted
    to revenue-bearing lines. Both are shown so the gap is visible rather than
    hidden. Gross profit covers Units and Parts lines only, the sole lines with a
    cost basis, and the margin is taken over that costed revenue to match the KPI.
    """
    return f"""
    WITH invoice_totals AS (
        SELECT
            d.InvoiceDocId                                             AS invoice_doc_id,
            COUNT(*)                                                   AS lines,
            SUM(CASE WHEN d.ItemType NOT IN {NON_REVENUE_ITEM_TYPES}
                     THEN d.NetExt ELSE 0 END)                         AS revenue,
            SUM(CASE WHEN d.ItemType IN {COSTED_ITEM_TYPES}
                     THEN d.NetExt ELSE 0 END)                         AS costed_revenue,
            SUM(CASE WHEN d.ItemType IN {COSTED_ITEM_TYPES}
                     THEN d.NetExt - ({_cost_case()}) ELSE 0 END)      AS gross_profit,
            SUM(CASE WHEN d.ItemType = 'TR' THEN -d.NetExt ELSE 0 END) AS trade_in_allowance
        FROM InvoiceDetail d
        JOIN InvoiceHeader ih ON ih.InvoiceDocId = d.InvoiceDocId
        WHERE ih.Status IN {BOOKED_STATUSES}
          AND ih.ActivityDate >= :start AND ih.ActivityDate <= :end
        GROUP BY d.InvoiceDocId
        HAVING SUM(CASE WHEN d.ItemType NOT IN {NON_REVENUE_ITEM_TYPES} THEN 1 ELSE 0 END) > 0
    ),
    invoices AS (
        SELECT
            date(h.ActivityDate)                            AS activity_date,
            COALESCE(NULLIF(TRIM(h.InvoiceNo), ''), '(none)') AS invoice_no,
            COALESCE(NULLIF(TRIM(h.DocNo), ''), '(none)')     AS doc_no,
            h.Status                                        AS status,
            COALESCE(NULLIF(TRIM(h.InvoiceType), ''), '??') AS invoice_type,
            TRIM(h.CustomerNo)                              AS customer_no,
            h.CustomerName                                  AS customer_name,
            TRIM(h.SalesPersonName)                         AS salesperson,
            t.lines                                         AS lines,
            t.revenue                                       AS revenue,
            t.gross_profit                                  AS gross_profit,
            t.trade_in_allowance                            AS trade_in_allowance,
            h.TotalInvoice                                  AS total_invoice,
            CASE WHEN t.costed_revenue <> 0
                 THEN t.gross_profit / t.costed_revenue * 100 END AS margin_pct
        FROM InvoiceHeader h
        JOIN invoice_totals t ON t.invoice_doc_id = h.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start AND h.ActivityDate <= :end
    )
    """


_INVOICE_SORTS = {
    "activity_date": "activity_date",
    "invoice_no": "invoice_no",
    "doc_no": "doc_no",
    "status": "status",
    "invoice_type": "invoice_type",
    "customer_no": "customer_no",
    "customer_name": "customer_name",
    "salesperson": "salesperson",
    "lines": "lines",
    "revenue": "revenue",
    "gross_profit": "gross_profit",
    "margin_pct": "margin_pct",
    "trade_in_allowance": "trade_in_allowance",
    "total_invoice": "total_invoice",
}


def invoices_detail(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    status: str | None = None,
    invoice_type: str | None = None,
    salesperson: str | None = None,
    customer_no: str | None = None,
    period: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    """The invoice register, searchable by invoice number or customer.

    Every filter is bound as a parameter, so a customer name carrying an
    apostrophe is matched as text and can never reach the SQL as syntax.
    """
    conditions: list[str] = []
    params: dict[str, Any] = dict(_bounds(start, end))
    _optional(conditions, params, "status = :status", "status", status)
    _optional(conditions, params, "invoice_type = :invoice_type", "invoice_type", invoice_type)
    _optional(conditions, params, "salesperson = :salesperson", "salesperson", salesperson)
    _optional(conditions, params, "customer_no = :customer_no", "customer_no", customer_no)
    # A trend bar hands over its own label, and the shape of that label follows
    # the grain it was drawn at: '2024', '2024-03' or '2024-Q1'. The first two are
    # prefixes of an ISO date, but a quarter label is not, so matching it as a
    # prefix silently returns nothing. Quarters are compared against the same
    # expression the trend groups by instead.
    if period and "Q" in period.upper():
        _optional(
            conditions,
            params,
            f"{_period_expr('quarter', 'activity_date')} = :period",
            "period",
            period.upper(),
        )
    else:
        _optional(
            conditions,
            params,
            "activity_date LIKE :period",
            "period",
            f"{period}%" if period else None,
        )
    _optional(
        conditions,
        params,
        "(invoice_no LIKE :search OR doc_no LIKE :search "
        "OR customer_name LIKE :search OR customer_no LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    {_invoice_register_cte()}
    SELECT * FROM invoices
    {_where(conditions)}
    """

    return _paged(
        "invoices_detail",
        body,
        _INVOICE_SORTS,
        "revenue",
        params,
        page,
        page_size,
        sort,
        direction,
    )


# ================================================================= UNITS/SALES


def _unit_lines_cte() -> str:
    """Booked unit-sale lines joined to their inventory record.

    Revenue is InvoiceDetail.NetExt rather than SaleUnit.NetExt so unit figures
    tie to the department totals; the two agree to the cent on all 4,926 booked
    unit lines. Cost is SaleUnit.InvoiceCost * Qty, the only unit cost basis.
    """
    return f"""
    WITH unit_lines AS (
        SELECT
            h.InvoiceDocId                          AS invoice_doc_id,
            h.DocNo                                 AS doc_no,
            h.ActivityDate                          AS activity_date,
            h.CustomerId                            AS customer_id,
            h.CustomerName                          AS customer_name,
            TRIM(h.SalesPersonName)                 AS salesperson,
            su.UnitId                               AS unit_id,
            su.StockNo                              AS stock_no,
            su.Qty                                  AS qty,
            COALESCE(NULLIF(TRIM(su.Model), ''), NULLIF(TRIM(ub.Model), ''),
                     '(unspecified)')               AS model,
            UPPER(COALESCE(NULLIF(TRIM(ub.Make), ''), 'Unspecified')) AS make_key,
            ub.Year                                 AS model_year,
            COALESCE(cat.DisplayText, 'Unclassified') AS category,
            CASE WHEN su.IsNew = 1 THEN 'New' ELSE 'Used' END AS condition_class,
            COALESCE(cond.DisplayText, 'Unrecorded') AS condition_grade,
            d.NetExt                                AS revenue,
            su.InvoiceCost * su.Qty                 AS cost
        FROM InvoiceHeader h
        JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId AND d.ItemType = 'UN'
        JOIN SaleUnit su ON su.ItemId = d.ItemId
        LEFT JOIN UnitBase ub ON ub.UnitId = su.UnitId
        LEFT JOIN UnitCategory cat ON TRIM(cat.UnitCategoryCode) = TRIM(su.CategoryCode)
        LEFT JOIN UnitCondition cond ON cond.UnitConditionId = ub.UnitConditionId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start AND h.ActivityDate <= :end
    )
    """


# Units physically on the lot. StockStatus is space-padded in this schema.
# IsActive separates live inventory (187 rows) from stale 'instock' records that
# were never cleared (273 rows carrying only $123k of recorded cost between
# them), so the live flag is required for an on-hand count to mean anything.
_ON_HAND = "TRIM(u.StockStatus) = 'instock' AND u.IsActive = 1"

# DateReceived is populated on only 71 of the 187 on-hand units, so age falls
# back to the purchase date and finally to the record's creation date.
_ON_HAND_AS_OF = (
    "COALESCE(NULLIF(u.DateReceived, ''), NULLIF(u.DatePurchased, ''), u.EntDate)"
)

AGING_BUCKETS = ["0-30", "31-60", "61-90", "91-180", "181-365", "365+"]

_AGING_CASE = """
    CASE
        WHEN age_days <  31 THEN '0-30'
        WHEN age_days <  61 THEN '31-60'
        WHEN age_days <  91 THEN '61-90'
        WHEN age_days < 181 THEN '91-180'
        WHEN age_days < 366 THEN '181-365'
        ELSE '365+'
    END
"""


def _on_hand_body() -> str:
    """On-hand units aged against the end of the selected window.

    A unit received after the window end was not yet on the lot then, so it is
    left out rather than reported with a negative age.
    """
    return f"""
    SELECT
        u.UnitId                                    AS unit_id,
        TRIM(u.StockNo)                             AS stock_no,
        COALESCE(NULLIF(TRIM(u.Make), ''), 'Unspecified') AS make,
        COALESCE(NULLIF(TRIM(u.Model), ''), '(unspecified)') AS model,
        u.Year                                      AS model_year,
        COALESCE(cat.DisplayText, 'Unclassified')   AS category,
        COALESCE(cond.DisplayText, 'Unrecorded')    AS condition_grade,
        u.Rental                                    AS is_rental,
        u.Attachment                                AS is_attachment,
        u.BaseCost                                  AS cost,
        u.BaseRetail                                AS retail,
        date({_ON_HAND_AS_OF})                      AS received,
        CASE WHEN NULLIF(u.DateReceived, '') IS NOT NULL THEN 'received'
             WHEN NULLIF(u.DatePurchased, '') IS NOT NULL THEN 'purchased'
             ELSE 'created' END                     AS age_basis,
        CAST(julianday(:as_of) - julianday({_ON_HAND_AS_OF}) AS INTEGER) AS age_days
    FROM UnitBase u
    LEFT JOIN UnitCategory cat ON cat.UnitCategoryId = u.UnitCategoryId
    LEFT JOIN UnitCondition cond ON cond.UnitConditionId = u.UnitConditionId
    WHERE {_ON_HAND}
      AND {_ON_HAND_AS_OF} <= :as_of
    """


def units_summary(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)

        sold = (
            db.query_one(
                f"""
                {_unit_lines_cte()}
                SELECT COUNT(*)                                  AS lines,
                       COALESCE(SUM(qty), 0)                     AS net_units,
                       COALESCE(SUM(revenue), 0)                 AS revenue,
                       COALESCE(SUM(cost), 0)                    AS cost,
                       SUM(CASE WHEN condition_class = 'New'  THEN 1 ELSE 0 END) AS new_lines,
                       SUM(CASE WHEN condition_class = 'Used' THEN 1 ELSE 0 END) AS used_lines,
                       COALESCE(SUM(CASE WHEN condition_class = 'New'
                                    THEN revenue ELSE 0 END), 0) AS new_revenue,
                       COALESCE(SUM(CASE WHEN condition_class = 'Used'
                                    THEN revenue ELSE 0 END), 0) AS used_revenue,
                       SUM(CASE WHEN qty < 0 THEN 1 ELSE 0 END)  AS return_lines,
                       SUM(CASE WHEN cost = 0 THEN 1 ELSE 0 END) AS zero_cost_lines,
                       COUNT(DISTINCT customer_id)               AS customers
                FROM unit_lines
                """,
                bounds,
            )
            or {}
        )

        trade = (
            db.query_one(
                f"""
                SELECT COUNT(*)                            AS trade_ins,
                       COALESCE(SUM(-d.NetExt), 0)         AS allowance,
                       COALESCE(SUM(t.OverAllowance), 0)   AS over_allowance,
                       COALESCE(SUM(t.InventoryValue), 0)  AS inventory_value
                FROM InvoiceHeader h
                JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId AND d.ItemType = 'TR'
                JOIN SaleUnitTradeIn t ON t.ItemId = d.ItemId
                WHERE h.Status IN {BOOKED_STATUSES}
                  AND h.ActivityDate >= :start AND h.ActivityDate <= :end
                """,
                bounds,
            )
            or {}
        )

        on_hand = (
            db.query_one(
                f"""
                SELECT COUNT(*)                     AS units,
                       COALESCE(SUM(cost), 0)       AS cost,
                       COALESCE(AVG(age_days), 0)   AS avg_age_days,
                       SUM(CASE WHEN age_days > 365 THEN 1 ELSE 0 END) AS over_year,
                       SUM(CASE WHEN cost > 0 THEN 1 ELSE 0 END)       AS with_cost
                FROM ({_on_hand_body()})
                """,
                {"as_of": end},
            )
            or {}
        )

        revenue = sold.get("revenue") or 0.0
        cost = sold.get("cost") or 0.0
        lines = sold.get("lines") or 0

        return {
            "revenue": revenue,
            "cost": cost,
            "gross_profit": revenue - cost,
            "margin_pct": _margin(revenue, cost),
            "units_sold": lines,
            "net_units": sold.get("net_units") or 0,
            "return_lines": sold.get("return_lines") or 0,
            "avg_selling_price": (revenue / lines) if lines else None,
            "new_lines": sold.get("new_lines") or 0,
            "used_lines": sold.get("used_lines") or 0,
            "new_revenue": sold.get("new_revenue") or 0.0,
            "used_revenue": sold.get("used_revenue") or 0.0,
            "new_mix_pct": ((sold.get("new_lines") or 0) / lines * 100) if lines else None,
            "zero_cost_lines": sold.get("zero_cost_lines") or 0,
            "customers": sold.get("customers") or 0,
            "trade_ins": trade.get("trade_ins") or 0,
            "trade_in_allowance": trade.get("allowance") or 0.0,
            "trade_in_over_allowance": trade.get("over_allowance") or 0.0,
            "trade_in_inventory_value": trade.get("inventory_value") or 0.0,
            "on_hand_units": on_hand.get("units") or 0,
            "on_hand_cost": on_hand.get("cost") or 0.0,
            "on_hand_with_cost": on_hand.get("with_cost") or 0,
            "on_hand_avg_age_days": on_hand.get("avg_age_days") or 0.0,
            "on_hand_over_year": on_hand.get("over_year") or 0,
            "on_hand_as_of": end,
        }

    return db.cached("units_summary", [start, end], build)


def units_charts(start: str, end: str, grain: str = "month") -> dict[str, Any]:
    grain = grain if grain in ("month", "quarter", "year") else "month"

    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)
        cte = _unit_lines_cte()

        def dimension(expr: str, alias: str, limit: int | None = None) -> list[dict[str, Any]]:
            rows = db.query(
                f"""
                {cte}
                SELECT {expr}                        AS {alias},
                       COUNT(*)                      AS units,
                       COALESCE(SUM(revenue), 0)     AS revenue,
                       COALESCE(SUM(cost), 0)        AS cost
                FROM unit_lines
                GROUP BY 1
                ORDER BY revenue DESC
                {f'LIMIT {limit}' if limit else ''}
                """,
                bounds,
            )
            for r in rows:
                r["gross_profit"] = (r["revenue"] or 0.0) - (r["cost"] or 0.0)
                r["margin_pct"] = _margin(r["revenue"], r["cost"])
                r["avg_price"] = (r["revenue"] / r["units"]) if r["units"] else None
            return rows

        makes = dimension("make_key", "make_key", 12)
        for r in makes:
            r["make"] = _titled(r.pop("make_key"))

        aging_rows = db.query(
            f"""
            SELECT {_AGING_CASE} AS bucket,
                   COUNT(*)                  AS units,
                   COALESCE(SUM(cost), 0)    AS cost,
                   COALESCE(SUM(retail), 0)  AS retail
            FROM ({_on_hand_body()})
            GROUP BY 1
            """,
            {"as_of": end},
        )
        by_bucket = {r["bucket"]: r for r in aging_rows}
        aging = [
            by_bucket.get(b, {"bucket": b, "units": 0, "cost": 0.0, "retail": 0.0})
            for b in AGING_BUCKETS
        ]

        trade_by_category = db.query(
            f"""
            SELECT COALESCE(cat.DisplayText, 'Unclassified') AS category,
                   COUNT(*)                                  AS trade_ins,
                   COALESCE(SUM(-d.NetExt), 0)               AS allowance,
                   COALESCE(SUM(t.OverAllowance), 0)         AS over_allowance,
                   COALESCE(SUM(t.InventoryValue), 0)        AS inventory_value
            FROM InvoiceHeader h
            JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId AND d.ItemType = 'TR'
            JOIN SaleUnitTradeIn t ON t.ItemId = d.ItemId
            LEFT JOIN UnitCategory cat ON cat.UnitCategoryId = t.CategoryId
            WHERE h.Status IN {BOOKED_STATUSES}
              AND h.ActivityDate >= :start AND h.ActivityDate <= :end
            GROUP BY 1
            ORDER BY allowance DESC
            """,
            bounds,
        )

        salespeople = db.query(
            f"""
            {cte}
            SELECT salesperson                       AS name,
                   COUNT(*)                          AS units,
                   COALESCE(SUM(revenue), 0)         AS revenue,
                   COALESCE(SUM(cost), 0)            AS cost,
                   COUNT(DISTINCT customer_id)       AS customers,
                   SUM(CASE WHEN condition_class = 'New' THEN 1 ELSE 0 END) AS new_units
            FROM unit_lines
            WHERE salesperson <> ''
            GROUP BY 1
            ORDER BY revenue DESC
            LIMIT 14
            """,
            bounds,
        )
        for r in salespeople:
            r["gross_profit"] = (r["revenue"] or 0.0) - (r["cost"] or 0.0)
            r["margin_pct"] = _margin(r["revenue"], r["cost"])
            r["avg_price"] = (r["revenue"] / r["units"]) if r["units"] else None

        period = _period_expr(grain, "activity_date")
        timeline = db.query(
            f"""
            {cte}
            SELECT {period}                          AS period,
                   COUNT(*)                          AS units,
                   COALESCE(SUM(revenue), 0)         AS revenue,
                   COALESCE(SUM(cost), 0)            AS cost,
                   SUM(CASE WHEN condition_class = 'New'  THEN 1 ELSE 0 END) AS new_units,
                   SUM(CASE WHEN condition_class = 'Used' THEN 1 ELSE 0 END) AS used_units
            FROM unit_lines
            GROUP BY 1
            ORDER BY 1
            """,
            bounds,
        )
        for r in timeline:
            r["margin_pct"] = _margin(r["revenue"], r["cost"])
            r["avg_price"] = (r["revenue"] / r["units"]) if r["units"] else None

        return {
            "grain": grain,
            "categories": dimension("category", "category"),
            "conditions": dimension("condition_class", "condition_class"),
            "grades": dimension("condition_grade", "condition_grade"),
            "makes": makes,
            "models": dimension("model", "model", 12),
            "aging": aging,
            "aging_buckets": AGING_BUCKETS,
            "trade_ins": trade_by_category,
            "salespeople": salespeople,
            "timeline": timeline,
        }

    return db.cached("units_charts", [start, end, grain], build)


_UNIT_DETAIL_SORTS = {
    "activity_date": "activity_date",
    "doc_no": "doc_no",
    "stock_no": "stock_no",
    # The row carries the grouping key; the display name is derived after paging.
    "make": "make_key",
    "model": "model",
    "model_year": "model_year",
    "category": "category",
    "condition_class": "condition_class",
    "condition_grade": "condition_grade",
    "customer_name": "customer_name",
    "salesperson": "salesperson",
    "qty": "qty",
    "revenue": "revenue",
    "cost": "cost",
    "gross_profit": "gross_profit",
    "margin_pct": "margin_pct",
}


def units_detail(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    category: str | None = None,
    condition: str | None = None,
    make: str | None = None,
    salesperson: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = dict(_bounds(start, end))
    _optional(conditions, params, "category = :category", "category", category)
    _optional(conditions, params, "condition_class = :condition", "condition", condition)
    _optional(conditions, params, "make_key = :make", "make", (make or "").upper() or None)
    _optional(conditions, params, "salesperson = :salesperson", "salesperson", salesperson)
    _optional(
        conditions,
        params,
        "(stock_no LIKE :search OR model LIKE :search OR customer_name LIKE :search "
        "OR doc_no LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    {_unit_lines_cte()}
    SELECT date(activity_date)          AS activity_date,
           doc_no,
           stock_no,
           make_key,
           model,
           model_year,
           category,
           condition_class,
           condition_grade,
           customer_name,
           salesperson,
           qty,
           revenue,
           cost,
           revenue - cost               AS gross_profit,
           CASE WHEN revenue <> 0 THEN (revenue - cost) / revenue * 100 END AS margin_pct
    FROM unit_lines
    {_where(conditions)}
    """

    # make_key exists so the grouping and the filter agree; the table shows a name.
    def named(rows: list[dict[str, Any]]) -> None:
        for r in rows:
            r["make"] = _titled(r.pop("make_key"))

    return _paged(
        "units_detail",
        body,
        _UNIT_DETAIL_SORTS,
        "activity_date",
        params,
        page,
        page_size,
        sort,
        direction,
        transform=named,
    )


_INVENTORY_SORTS = {
    "stock_no": "stock_no",
    "make": "make",
    "model": "model",
    "model_year": "model_year",
    "category": "category",
    "condition_grade": "condition_grade",
    "cost": "cost",
    "retail": "retail",
    "received": "received",
    "age_days": "age_days",
}


def units_inventory(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    bucket: str | None = None,
    category: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = {"as_of": end}
    _optional(conditions, params, f"{_AGING_CASE} = :bucket", "bucket", bucket)
    _optional(conditions, params, "category = :category", "category", category)
    _optional(
        conditions,
        params,
        "(stock_no LIKE :search OR model LIKE :search OR make LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    SELECT *, {_AGING_CASE} AS bucket
    FROM ({_on_hand_body()})
    {_where(conditions)}
    """
    return _paged(
        "units_inventory",
        body,
        _INVENTORY_SORTS,
        "age_days",
        params,
        page,
        page_size,
        sort,
        direction,
    )


# ======================================================================= PARTS


# Line-level margin bands. PartMaster.PriceClassId is 4 on all 16,208 parts and
# PartLocation.PriceClassId is NULL throughout, with no price-class table in the
# schema at all, so realised margin is used as the price-class proxy and is
# labelled as such in the UI.
MARGIN_BANDS = [
    "below cost",
    "0-15%",
    "15-25%",
    "25-35%",
    "35-50%",
    "50%+",
    "no cost basis",
    "credit / zero",
]

_MARGIN_BAND_CASE = """
    CASE
        WHEN revenue <= 0        THEN 'credit / zero'
        WHEN cost = 0            THEN 'no cost basis'
        WHEN (revenue - cost) / revenue < 0    THEN 'below cost'
        WHEN (revenue - cost) / revenue < 0.15 THEN '0-15%'
        WHEN (revenue - cost) / revenue < 0.25 THEN '15-25%'
        WHEN (revenue - cost) / revenue < 0.35 THEN '25-35%'
        WHEN (revenue - cost) / revenue < 0.50 THEN '35-50%'
        ELSE '50%+'
    END
"""

# Days since a part last sold. Bucketing happens in `_part_stock_rows` because
# that snapshot is materialised once and shared by three panels.
MOVEMENT_BUCKETS = [
    "<90 days",
    "90-180 days",
    "180-365 days",
    "1-2 years",
    "2+ years",
    "never sold",
]


def _part_lines_cte(dimensions: bool = True) -> str:
    """Booked parts lines, optionally carrying their master-file dimensions.

    PartLocation is exactly one row per part, so joining it cannot fan the line
    count out. Revenue is InvoiceDetail.NetExt: SalePart.NetExt disagrees on 11
    of 235,558 booked lines ($1,689 in total) and the detail row is the source
    the department totals use.

    The four dimension joins double the cost of scanning all 235k lines, so
    passes that group only by part or by period ask for `dimensions=False` and
    pick the descriptive columns up from the stock snapshot instead.
    """
    dimension_columns = """,
            COALESCE(pg.DisplayText, 'Unclassified')    AS part_group,
            COALESCE(mfg.DisplayText, 'Unknown')        AS manufacturer,
            COALESCE(NULLIF(TRIM(pl.OFCCode), ''), 'unset') AS stocking_class,
            COALESCE(NULLIF(TRIM(pl.Bin), ''), '(no bin)')  AS bin"""
    dimension_joins = """
        LEFT JOIN PartMaster pm ON pm.PartId = sp.PartId
        LEFT JOIN PartGroup pg ON pg.PartGroupId = pm.PartGroupId
        LEFT JOIN PartManufacturer mfg ON mfg.MfgId = COALESCE(sp.MfgId, pm.MfgId)
        LEFT JOIN PartLocation pl ON pl.PartId = sp.PartId"""

    return f"""
    WITH part_lines AS (
        SELECT
            h.InvoiceDocId                              AS invoice_doc_id,
            h.DocNo                                     AS doc_no,
            h.ActivityDate                              AS activity_date,
            h.CustomerId                                AS customer_id,
            h.CustomerName                              AS customer_name,
            sp.PartId                                   AS part_id,
            TRIM(sp.PartNo)                             AS part_no,
            COALESCE(NULLIF(TRIM(sp.Description), ''), '(no description)') AS description,
            sp.Qty                                      AS qty,
            sp.QtyRequested                             AS qty_requested,
            sp.UnitPrice                                AS unit_price,
            sp.AvgCost                                  AS avg_cost,
            sp.DiscountAmt                              AS discount_amt,
            sp.DealerListPrice                          AS list_price,
            d.NetExt                                    AS revenue,
            sp.AvgCost * sp.Qty                         AS cost{dimension_columns if dimensions else ''}
        FROM InvoiceHeader h
        JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId AND d.ItemType = 'PA'
        JOIN SalePart sp ON sp.ItemId = d.ItemId{dimension_joins if dimensions else ''}
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start AND h.ActivityDate <= :end
    )
    """


def _part_stock_body() -> str:
    """The parts master with its last booked sale, for movement analysis.

    This is a stock snapshot rather than a windowed figure: recency is measured
    against the end of the selected range so the filter bar still moves it.
    """
    return f"""
    SELECT
        pm.PartId                                       AS part_id,
        TRIM(pm.PartNo)                                 AS part_no,
        COALESCE(NULLIF(TRIM(pm.Description), ''), '(no description)') AS description,
        COALESCE(pg.DisplayText, 'Unclassified')        AS part_group,
        COALESCE(mfg.DisplayText, 'Unknown')            AS manufacturer,
        COALESCE(NULLIF(TRIM(pl.OFCCode), ''), 'unset') AS stocking_class,
        COALESCE(NULLIF(TRIM(pl.Bin), ''), '(no bin)')  AS bin,
        pl.MinStock                                     AS min_stock,
        pl.MaxStock                                     AS max_stock,
        pm.PartStatus                                   AS part_status,
        s.last_sold                                     AS last_sold,
        COALESCE(s.lines, 0)                            AS lines,
        COALESCE(s.qty, 0)                              AS qty,
        COALESCE(s.revenue, 0)                          AS revenue,
        COALESCE(s.cost, 0)                             AS cost,
        CAST(julianday(:as_of) - julianday(s.last_sold) AS INTEGER) AS days_since
    FROM PartMaster pm
    LEFT JOIN PartGroup pg ON pg.PartGroupId = pm.PartGroupId
    LEFT JOIN PartManufacturer mfg ON mfg.MfgId = pm.MfgId
    LEFT JOIN PartLocation pl ON pl.PartId = pm.PartId
    LEFT JOIN (
        SELECT sp.PartId                    AS part_id,
               MAX(date(h.ActivityDate))    AS last_sold,
               COUNT(*)                     AS lines,
               SUM(sp.Qty)                  AS qty,
               SUM(d.NetExt)                AS revenue,
               SUM(sp.AvgCost * sp.Qty)     AS cost
        FROM SalePart sp
        JOIN InvoiceDetail d ON d.ItemId = sp.ItemId AND d.ItemType = 'PA'
        JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES} AND h.ActivityDate <= :as_of_end
        GROUP BY sp.PartId
    ) s ON s.part_id = pm.PartId
    WHERE pm.IsActive = 1
    """


def _part_stock_rows(start: str, end: str) -> list[dict[str, Any]]:
    """The parts stock snapshot, materialised once per window.

    Three panels read this and it takes about 1.5s to build, so it is held as a
    list rather than re-queried; 16,113 rows is small enough to sort and filter
    in memory.
    """

    def build() -> list[dict[str, Any]]:
        rows = db.query(
            _part_stock_body(), {"as_of": end, "as_of_end": f"{end} 23:59:59"}
        )
        for r in rows:
            days = r["days_since"]
            if r["last_sold"] is None:
                r["bucket"] = "never sold"
            elif days < 90:
                r["bucket"] = "<90 days"
            elif days < 181:
                r["bucket"] = "90-180 days"
            elif days < 366:
                r["bucket"] = "180-365 days"
            elif days < 731:
                r["bucket"] = "1-2 years"
            else:
                r["bucket"] = "2+ years"
            r["margin_pct"] = _margin(r["revenue"], r["cost"])
        return rows

    return db.cached("part_stock_rows", [end], build)


def parts_summary(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)

        sold = (
            db.query_one(
                f"""
                {_part_lines_cte()}
                SELECT COUNT(*)                              AS lines,
                       COUNT(DISTINCT part_id)               AS parts_sold,
                       COUNT(DISTINCT invoice_doc_id)        AS invoices,
                       COALESCE(SUM(qty), 0)                 AS qty,
                       COALESCE(SUM(qty_requested), 0)       AS qty_requested,
                       COALESCE(SUM(revenue), 0)             AS revenue,
                       COALESCE(SUM(cost), 0)                AS cost,
                       COALESCE(SUM(discount_amt), 0)        AS discount,
                       SUM(CASE WHEN qty < qty_requested THEN 1 ELSE 0 END) AS short_lines,
                       SUM(CASE WHEN cost = 0 THEN 1 ELSE 0 END)            AS zero_cost_lines,
                       SUM(CASE WHEN revenue > 0 AND cost > revenue THEN 1 ELSE 0 END) AS below_cost_lines
                FROM part_lines
                """,
                bounds,
            )
            or {}
        )

        stock_rows = _part_stock_rows(start, end)
        stock = {
            "parts_on_file": len(stock_rows),
            "never_sold": sum(1 for r in stock_rows if r["last_sold"] is None),
            "stocked": sum(1 for r in stock_rows if r["stocking_class"] == "minmax"),
            "dormant_over_year": sum(
                1
                for r in stock_rows
                if r["last_sold"] is not None and (r["days_since"] or 0) > 365
            ),
            "with_bin": sum(1 for r in stock_rows if r["bin"] != "(no bin)"),
        }

        revenue = sold.get("revenue") or 0.0
        cost = sold.get("cost") or 0.0
        lines = sold.get("lines") or 0
        qty = sold.get("qty") or 0.0
        requested = sold.get("qty_requested") or 0.0

        return {
            "revenue": revenue,
            "cost": cost,
            "gross_profit": revenue - cost,
            "margin_pct": _margin(revenue, cost),
            "lines": lines,
            "parts_sold": sold.get("parts_sold") or 0,
            "invoices": sold.get("invoices") or 0,
            "qty": qty,
            "avg_line_value": (revenue / lines) if lines else None,
            "discount": sold.get("discount") or 0.0,
            # Delivered against requested quantity. A proxy for fill rate: the
            # schema records no separate backorder or lost-sale row.
            "fill_rate_pct": (qty / requested * 100) if requested else None,
            "short_lines": sold.get("short_lines") or 0,
            "zero_cost_lines": sold.get("zero_cost_lines") or 0,
            "below_cost_lines": sold.get("below_cost_lines") or 0,
            "parts_on_file": stock.get("parts_on_file") or 0,
            "never_sold": stock.get("never_sold") or 0,
            "stocked_parts": stock.get("stocked") or 0,
            "dormant_over_year": stock.get("dormant_over_year") or 0,
            "with_bin": stock.get("with_bin") or 0,
            "as_of": end,
        }

    return db.cached("parts_summary", [start, end], build)


def parts_charts(start: str, end: str, grain: str = "month") -> dict[str, Any]:
    grain = grain if grain in ("month", "quarter", "year") else "month"

    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)
        cte = _part_lines_cte()

        # One pass at the finest grain, rolled up in Python afterwards. Grouping
        # the four dimensions together yields well under 200 rows, so this
        # replaces four separate scans of the 235k-line parts table.
        grid = db.query(
            f"""
            {cte}
            SELECT part_group,
                   manufacturer,
                   stocking_class,
                   {_MARGIN_BAND_CASE}          AS band,
                   COUNT(*)                     AS lines,
                   COALESCE(SUM(qty), 0)        AS qty,
                   COALESCE(SUM(revenue), 0)    AS revenue,
                   COALESCE(SUM(cost), 0)       AS cost
            FROM part_lines
            GROUP BY 1, 2, 3, 4
            """,
            bounds,
        )

        def rollup(field: str, order: list[str] | None = None) -> list[dict[str, Any]]:
            acc: dict[str, dict[str, Any]] = {}
            for src in grid:
                bucket = acc.setdefault(
                    src[field],
                    {field: src[field], "lines": 0, "qty": 0.0, "revenue": 0.0, "cost": 0.0},
                )
                bucket["lines"] += src["lines"]
                bucket["qty"] += src["qty"] or 0.0
                bucket["revenue"] += src["revenue"] or 0.0
                bucket["cost"] += src["cost"] or 0.0
            if order:
                for key in order:
                    acc.setdefault(
                        key, {field: key, "lines": 0, "qty": 0.0, "revenue": 0.0, "cost": 0.0}
                    )
            rows = (
                [acc[k] for k in order]
                if order
                else sorted(acc.values(), key=lambda r: r["revenue"], reverse=True)
            )
            for r in rows:
                r["gross_profit"] = r["revenue"] - r["cost"]
                r["margin_pct"] = _margin(r["revenue"], r["cost"])
            return rows

        # Grouped by part with no dimension joins, then described from the stock
        # snapshot. This one pass feeds both the top-mover ranking and the shelf
        # breakdown, since a part's bin comes from the snapshot too.
        by_part = db.query(
            f"""
            {_part_lines_cte(dimensions=False)}
            SELECT part_id,
                   MAX(part_no)                 AS part_no,
                   MAX(description)             AS description,
                   COUNT(*)                     AS lines,
                   COALESCE(SUM(qty), 0)        AS qty,
                   COALESCE(SUM(revenue), 0)    AS revenue,
                   COALESCE(SUM(cost), 0)       AS cost
            FROM part_lines
            GROUP BY part_id
            """,
            bounds,
        )
        described = {r["part_id"]: r for r in _part_stock_rows(start, end)}

        movers = []
        for src in sorted(by_part, key=lambda r: r["revenue"] or 0.0, reverse=True)[:15]:
            master = described.get(src["part_id"], {})
            movers.append(
                {
                    "part_no": src["part_no"],
                    "description": src["description"],
                    "part_group": master.get("part_group", "Unclassified"),
                    "lines": src["lines"],
                    "qty": src["qty"],
                    "revenue": src["revenue"],
                    "cost": src["cost"],
                    "gross_profit": (src["revenue"] or 0.0) - (src["cost"] or 0.0),
                    "margin_pct": _margin(src["revenue"], src["cost"]),
                }
            )

        movement = [
            {"bucket": b, "parts": 0, "stocked": 0, "revenue": 0.0} for b in MOVEMENT_BUCKETS
        ]
        by_bucket = {r["bucket"]: r for r in movement}
        for src in _part_stock_rows(start, end):
            bucket = by_bucket[src["bucket"]]
            bucket["parts"] += 1
            bucket["stocked"] += 1 if src["stocking_class"] == "minmax" else 0
            bucket["revenue"] += src["revenue"] or 0.0

        # Shelf detail comes from PartLocation.Bin, not SalePart.ShelfLocation:
        # that column is blank on 236,269 of 248,162 rows and the rest holds
        # customer names and phone numbers rather than locations.
        by_bin: dict[str, dict[str, Any]] = {}
        for src in by_part:
            bin_name = described.get(src["part_id"], {}).get("bin", "(no bin)")
            if bin_name == "(no bin)":
                continue
            bucket = by_bin.setdefault(
                bin_name,
                {"bin": bin_name, "lines": 0, "parts": 0, "qty": 0.0, "revenue": 0.0, "cost": 0.0},
            )
            bucket["lines"] += src["lines"]
            bucket["parts"] += 1
            bucket["qty"] += src["qty"] or 0.0
            bucket["revenue"] += src["revenue"] or 0.0
            bucket["cost"] += src["cost"] or 0.0
        locations = sorted(by_bin.values(), key=lambda r: r["revenue"], reverse=True)[:15]
        for r in locations:
            r["gross_profit"] = r["revenue"] - r["cost"]
            r["margin_pct"] = _margin(r["revenue"], r["cost"])

        period = _period_expr(grain, "activity_date")
        timeline = db.query(
            f"""
            {_part_lines_cte(dimensions=False)}
            SELECT {period}                          AS period,
                   COUNT(*)                          AS lines,
                   COALESCE(SUM(qty), 0)             AS qty,
                   COALESCE(SUM(revenue), 0)         AS revenue,
                   COALESCE(SUM(cost), 0)            AS cost
            FROM part_lines
            GROUP BY 1
            ORDER BY 1
            """,
            bounds,
        )
        for r in timeline:
            r["gross_profit"] = (r["revenue"] or 0.0) - (r["cost"] or 0.0)
            r["margin_pct"] = _margin(r["revenue"], r["cost"])

        return {
            "grain": grain,
            "groups": rollup("part_group"),
            "manufacturers": rollup("manufacturer"),
            "stocking_classes": rollup("stocking_class"),
            "movers": movers,
            "bands": rollup("band", MARGIN_BANDS),
            "margin_bands": MARGIN_BANDS,
            "movement": movement,
            "movement_buckets": MOVEMENT_BUCKETS,
            "locations": locations,
            "timeline": timeline,
        }

    return db.cached("parts_charts", [start, end, grain], build)


_PART_DETAIL_SORTS = {
    "activity_date": "activity_date",
    "doc_no": "doc_no",
    "part_no": "part_no",
    "description": "description",
    "part_group": "part_group",
    "manufacturer": "manufacturer",
    "bin": "bin",
    "customer_name": "customer_name",
    "qty": "qty",
    "unit_price": "unit_price",
    "avg_cost": "avg_cost",
    "revenue": "revenue",
    "cost": "cost",
    "gross_profit": "gross_profit",
    "margin_pct": "margin_pct",
}


def parts_detail(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    part_group: str | None = None,
    manufacturer: str | None = None,
    stocking_class: str | None = None,
    band: str | None = None,
    bin: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = dict(_bounds(start, end))
    _optional(conditions, params, "part_group = :part_group", "part_group", part_group)
    _optional(conditions, params, "manufacturer = :manufacturer", "manufacturer", manufacturer)
    _optional(
        conditions, params, "stocking_class = :stocking_class", "stocking_class", stocking_class
    )
    _optional(conditions, params, f"{_MARGIN_BAND_CASE} = :band", "band", band)
    _optional(conditions, params, "bin = :bin", "bin", bin)
    _optional(
        conditions,
        params,
        "(part_no LIKE :search OR description LIKE :search OR customer_name LIKE :search "
        "OR doc_no LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    {_part_lines_cte()}
    SELECT date(activity_date)      AS activity_date,
           doc_no,
           part_no,
           description,
           part_group,
           manufacturer,
           bin,
           customer_name,
           qty,
           unit_price,
           avg_cost,
           discount_amt,
           revenue,
           cost,
           revenue - cost           AS gross_profit,
           CASE WHEN revenue <> 0 THEN (revenue - cost) / revenue * 100 END AS margin_pct
    FROM part_lines
    {_where(conditions)}
    """
    return _paged(
        "parts_detail",
        body,
        _PART_DETAIL_SORTS,
        "revenue",
        params,
        page,
        page_size,
        sort,
        direction,
    )


_PART_STOCK_SORTS = (
    "part_no",
    "description",
    "part_group",
    "manufacturer",
    "stocking_class",
    "bin",
    "min_stock",
    "max_stock",
    "last_sold",
    "days_since",
    "lines",
    "qty",
    "revenue",
    "margin_pct",
)


def parts_stock(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    bucket: str | None = None,
    part_group: str | None = None,
    stocking_class: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    rows = _part_stock_rows(start, end)
    needle = (search or "").lower()

    def keep(r: dict[str, Any]) -> bool:
        if bucket and r["bucket"] != bucket:
            return False
        if part_group and r["part_group"] != part_group:
            return False
        if stocking_class and r["stocking_class"] != stocking_class:
            return False
        if needle and needle not in f"{r['part_no']} {r['description']}".lower():
            return False
        return True

    return _page_rows(
        [r for r in rows if keep(r)],
        _PART_STOCK_SORTS,
        "days_since",
        page,
        page_size,
        sort,
        direction,
    )


# ===================================================================== SERVICE


# Service carries no cost basis anywhere in the schema: AppUser.HourlyRate is 0
# for all 48 users, so labour cost cannot be derived and every figure below is
# revenue, hours or a count. Nothing here is a margin.
SERVICE_HAS_COST_BASIS = False

WIP_AGE_BUCKETS = ["0-7 days", "8-30 days", "31-90 days", "91-365 days", "365+ days"]

_WIP_AGE_CASE = """
    CASE
        WHEN age_days <   8 THEN '0-7 days'
        WHEN age_days <  31 THEN '8-30 days'
        WHEN age_days <  91 THEN '31-90 days'
        WHEN age_days < 366 THEN '91-365 days'
        ELSE '365+ days'
    END
"""

_TECH_NAME = (
    "COALESCE(NULLIF(TRIM(u.FirstName || ' ' || u.LastName), ''), u.UserName, "
    "'Tech ' || {col})"
)


def _segment_lines_cte() -> str:
    """Booked labour segments with their work-order context.

    InvoiceSegment joins the invoice through `InvDocId`, not `InvoiceDocId`. The
    assigned technician is InvoiceHeader.WOTechId, populated on 11,263 of 15,160
    booked work orders; clocked time per technician comes from WorkInProgress
    instead and is reported separately.
    """
    return f"""
    WITH segments AS (
        SELECT
            h.InvoiceDocId                              AS invoice_doc_id,
            h.DocNo                                     AS doc_no,
            h.ActivityDate                              AS activity_date,
            h.CustomerId                                AS customer_id,
            h.CustomerName                              AS customer_name,
            h.InvoiceType                               AS invoice_type,
            COALESCE(ws.DisplayText, 'Unset')           AS wo_status,
            {_TECH_NAME.format(col='h.WOTechId')}       AS tech,
            s.SegmentId                                 AS segment_id,
            COALESCE(NULLIF(TRIM(s.DisplayText), ''), '(no description)') AS description,
            TRIM(s.BillAs)                              AS bill_as,
            TRIM(LOWER(s.Status))                       AS segment_status,
            COALESCE(s.ActualHrs, 0)                    AS actual_hrs,
            COALESCE(s.FlatRateLaborHrs, 0)             AS flat_rate_hrs,
            s.LaborRate                                 AS labor_rate,
            COALESCE(s.ShopFeeAmt, 0)                   AS shop_fee,
            COALESCE(NULLIF(TRIM(s.WOUnitModel), ''), '(none)') AS unit_model,
            s.WOMeter                                   AS meter,
            s.HasParts                                  AS has_parts,
            d.NetExt                                    AS revenue
        FROM InvoiceHeader h
        JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId AND d.ItemType = 'SL'
        JOIN InvoiceSegment s ON s.ItemId = d.ItemId
        LEFT JOIN SettingsWorkOrderStatus ws ON ws.WorkOrderStatusId = h.WOStatusId
        LEFT JOIN AppUser u ON u.AppUserId = h.WOTechId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start AND h.ActivityDate <= :end
    )
    """


def _wip_body() -> str:
    """Work orders still carrying an open labour segment.

    'Open' is the segment's own status. Most of these sit on invoices that were
    already finalized, so this is a housekeeping backlog rather than unbilled
    work; the two are separated by `doc_status` so neither is read as the other.
    """
    return f"""
    SELECT
        h.InvoiceDocId                              AS invoice_doc_id,
        h.DocNo                                     AS doc_no,
        date(h.ActivityDate)                        AS activity_date,
        h.Status                                    AS doc_status,
        COALESCE(ws.DisplayText, 'Unset')           AS wo_status,
        {_TECH_NAME.format(col='h.WOTechId')}       AS tech,
        h.CustomerName                              AS customer_name,
        COALESCE(NULLIF(TRIM(h.WOUnitModel), ''), '(none)') AS unit_model,
        COUNT(*)                                    AS open_segments,
        COALESCE(SUM(s.ActualHrs), 0)               AS actual_hrs,
        COALESCE(SUM(s.NetExt), 0)                  AS open_value,
        CAST(julianday(:as_of) - julianday(h.ActivityDate) AS INTEGER) AS age_days
    FROM InvoiceSegment s
    JOIN InvoiceHeader h ON h.InvoiceDocId = s.InvDocId
    LEFT JOIN SettingsWorkOrderStatus ws ON ws.WorkOrderStatusId = h.WOStatusId
    LEFT JOIN AppUser u ON u.AppUserId = h.WOTechId
    WHERE TRIM(LOWER(s.Status)) = 'open'
      AND h.Status <> 'voided'
      AND h.ActivityDate <= :as_of_end
    GROUP BY h.InvoiceDocId
    """


def service_summary(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)

        billed = (
            db.query_one(
                f"""
                {_segment_lines_cte()}
                SELECT COUNT(*)                             AS segments,
                       COUNT(DISTINCT invoice_doc_id)       AS work_orders,
                       COUNT(DISTINCT customer_id)          AS customers,
                       COALESCE(SUM(revenue), 0)            AS revenue,
                       COALESCE(SUM(actual_hrs), 0)         AS actual_hrs,
                       COALESCE(SUM(flat_rate_hrs), 0)      AS flat_rate_hrs,
                       COALESCE(SUM(shop_fee), 0)           AS shop_fees,
                       COALESCE(AVG(NULLIF(labor_rate, 0)), 0) AS avg_labor_rate,
                       SUM(CASE WHEN bill_as = 'flatrate' THEN 1 ELSE 0 END) AS flat_rate_segments,
                       SUM(CASE WHEN segment_status = 'open' THEN 1 ELSE 0 END) AS open_segments
                FROM segments
                """,
                bounds,
            )
            or {}
        )

        # Clocked time is filtered on its own timestamp: a technician's hours are
        # worked whether or not the invoice has been finalized yet.
        clocked = (
            db.query_one(
                """
                SELECT COALESCE(SUM(ElapsedHours), 0) AS clock_hours,
                       COUNT(DISTINCT TechId)         AS techs,
                       COUNT(*)                       AS punches
                FROM WorkInProgress
                WHERE TimeOn >= :start AND TimeOn <= :end AND IsActive = 1
                """,
                bounds,
            )
            or {}
        )

        wip = (
            db.query_one(
                f"""
                SELECT COUNT(*)                          AS work_orders,
                       COALESCE(SUM(open_segments), 0)   AS segments,
                       COALESCE(SUM(open_value), 0)      AS value,
                       COALESCE(AVG(age_days), 0)        AS avg_age_days,
                       SUM(CASE WHEN doc_status IN ('finalized', 'archived')
                                THEN 1 ELSE 0 END)       AS on_booked_docs
                FROM ({_wip_body()})
                """,
                {"as_of": end, "as_of_end": f"{end} 23:59:59"},
            )
            or {}
        )

        revenue = billed.get("revenue") or 0.0
        actual = billed.get("actual_hrs") or 0.0
        clock = clocked.get("clock_hours") or 0.0
        work_orders = billed.get("work_orders") or 0
        segments = billed.get("segments") or 0

        return {
            "revenue": revenue,
            "segments": segments,
            "work_orders": work_orders,
            "customers": billed.get("customers") or 0,
            "actual_hrs": actual,
            "flat_rate_hrs": billed.get("flat_rate_hrs") or 0.0,
            "shop_fees": billed.get("shop_fees") or 0.0,
            "avg_labor_rate": billed.get("avg_labor_rate") or 0.0,
            "effective_rate": (revenue / actual) if actual else None,
            "hours_per_wo": (actual / work_orders) if work_orders else None,
            "revenue_per_wo": (revenue / work_orders) if work_orders else None,
            "segments_per_wo": (segments / work_orders) if work_orders else None,
            "flat_rate_share_pct": (
                (billed.get("flat_rate_segments") or 0) / segments * 100 if segments else None
            ),
            "clock_hours": clock,
            "techs_clocked": clocked.get("techs") or 0,
            "punches": clocked.get("punches") or 0,
            # Share of clocked time that reached an invoice. Can exceed 100% on
            # flat-rate work, where billed hours are quoted rather than measured.
            "billed_vs_clocked_pct": (actual / clock * 100) if clock else None,
            "wip_work_orders": wip.get("work_orders") or 0,
            "wip_segments": wip.get("segments") or 0,
            "wip_value": wip.get("value") or 0.0,
            "wip_avg_age_days": wip.get("avg_age_days") or 0.0,
            "wip_on_booked_docs": wip.get("on_booked_docs") or 0,
            "has_cost_basis": SERVICE_HAS_COST_BASIS,
            "as_of": end,
        }

    return db.cached("service_summary", [start, end], build)


def service_charts(start: str, end: str, grain: str = "month") -> dict[str, Any]:
    grain = grain if grain in ("month", "quarter", "year") else "month"

    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)
        cte = _segment_lines_cte()
        period = _period_expr(grain, "activity_date")

        throughput = db.query(
            f"""
            {cte}
            SELECT {period}                             AS period,
                   COUNT(DISTINCT invoice_doc_id)       AS work_orders,
                   COUNT(*)                             AS segments,
                   COALESCE(SUM(actual_hrs), 0)         AS actual_hrs,
                   COALESCE(SUM(flat_rate_hrs), 0)      AS flat_rate_hrs,
                   COALESCE(SUM(revenue), 0)            AS revenue
            FROM segments
            GROUP BY 1
            ORDER BY 1
            """,
            bounds,
        )
        for r in throughput:
            r["effective_rate"] = (
                (r["revenue"] / r["actual_hrs"]) if r["actual_hrs"] else None
            )

        # Technician productivity is clocked time from WorkInProgress. Billed
        # hours are attributed to the segment the punch belongs to, so a segment
        # worked by two technicians contributes to both.
        techs = db.query(
            f"""
            SELECT {_TECH_NAME.format(col='w.TechId')}       AS name,
                   u.IsServiceTech                           AS is_service_tech,
                   COALESCE(SUM(w.ElapsedHours), 0)          AS clock_hours,
                   COUNT(*)                                  AS punches,
                   COUNT(DISTINCT w.SegmentId)               AS segments,
                   COALESCE(SUM(CASE WHEN w.IsClock = 1 THEN w.ElapsedHours ELSE 0 END), 0)
                                                             AS clocked_in_hours,
                   COALESCE((SELECT SUM(s2.NetExt) FROM InvoiceSegment s2
                             WHERE s2.SegmentId IN (
                                 SELECT w2.SegmentId FROM WorkInProgress w2
                                 WHERE w2.TechId = w.TechId AND w2.IsActive = 1
                                   AND w2.TimeOn >= :start AND w2.TimeOn <= :end)), 0)
                                                             AS segment_revenue
            FROM WorkInProgress w
            LEFT JOIN AppUser u ON u.AppUserId = w.TechId
            WHERE w.TimeOn >= :start AND w.TimeOn <= :end AND w.IsActive = 1
            GROUP BY w.TechId
            ORDER BY clock_hours DESC
            LIMIT 16
            """,
            bounds,
        )
        for r in techs:
            r["revenue_per_hour"] = (
                (r["segment_revenue"] / r["clock_hours"]) if r["clock_hours"] else None
            )

        billing_mix = db.query(
            f"""
            {cte}
            SELECT CASE WHEN bill_as = 'flatrate' THEN 'Flat rate' ELSE 'Actual hours' END AS bill_as,
                   COUNT(*)                             AS segments,
                   COALESCE(SUM(actual_hrs), 0)         AS actual_hrs,
                   COALESCE(SUM(flat_rate_hrs), 0)      AS flat_rate_hrs,
                   COALESCE(SUM(revenue), 0)            AS revenue
            FROM segments
            GROUP BY 1
            ORDER BY revenue DESC
            """,
            bounds,
        )
        for r in billing_mix:
            r["effective_rate"] = (
                (r["revenue"] / r["actual_hrs"]) if r["actual_hrs"] else None
            )
            # Above 100% the shop billed more hours than it worked.
            r["realization_pct"] = (
                (r["flat_rate_hrs"] / r["actual_hrs"] * 100) if r["actual_hrs"] else None
            )

        pipeline = db.query(
            f"""
            {cte}
            SELECT wo_status                            AS wo_status,
                   COUNT(DISTINCT invoice_doc_id)       AS work_orders,
                   COUNT(*)                             AS segments,
                   COALESCE(SUM(actual_hrs), 0)         AS actual_hrs,
                   COALESCE(SUM(revenue), 0)            AS revenue
            FROM segments
            GROUP BY 1
            ORDER BY work_orders DESC
            LIMIT 14
            """,
            bounds,
        )

        snapshot = {"as_of": end, "as_of_end": f"{end} 23:59:59"}
        wip_rows = db.query(
            f"""
            SELECT {_WIP_AGE_CASE}                      AS bucket,
                   COUNT(*)                             AS work_orders,
                   COALESCE(SUM(open_segments), 0)      AS segments,
                   COALESCE(SUM(open_value), 0)         AS value,
                   SUM(CASE WHEN doc_status IN ('finalized', 'archived') THEN 1 ELSE 0 END)
                                                        AS on_booked_docs
            FROM ({_wip_body()})
            GROUP BY 1
            """,
            snapshot,
        )
        by_bucket = {r["bucket"]: r for r in wip_rows}
        wip_aging = [
            by_bucket.get(
                b,
                {"bucket": b, "work_orders": 0, "segments": 0, "value": 0.0, "on_booked_docs": 0},
            )
            for b in WIP_AGE_BUCKETS
        ]

        return {
            "grain": grain,
            "throughput": throughput,
            "technicians": techs,
            "billing_mix": billing_mix,
            "pipeline": pipeline,
            "wip_aging": wip_aging,
            "wip_buckets": WIP_AGE_BUCKETS,
            "has_cost_basis": SERVICE_HAS_COST_BASIS,
        }

    return db.cached("service_charts", [start, end, grain], build)


_SERVICE_DETAIL_SORTS = {
    "activity_date": "activity_date",
    "doc_no": "doc_no",
    "customer_name": "customer_name",
    "tech": "tech",
    "wo_status": "wo_status",
    "bill_as": "bill_as",
    "segment_status": "segment_status",
    "unit_model": "unit_model",
    "actual_hrs": "actual_hrs",
    "flat_rate_hrs": "flat_rate_hrs",
    "labor_rate": "labor_rate",
    "shop_fee": "shop_fee",
    "meter": "meter",
    "revenue": "revenue",
    "effective_rate": "effective_rate",
}


def service_detail(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    bill_as: str | None = None,
    wo_status: str | None = None,
    tech: str | None = None,
    segment_status: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = dict(_bounds(start, end))
    _optional(
        conditions,
        params,
        "CASE WHEN bill_as = 'flatrate' THEN 'Flat rate' ELSE 'Actual hours' END = :bill_as",
        "bill_as",
        bill_as,
    )
    _optional(conditions, params, "wo_status = :wo_status", "wo_status", wo_status)
    _optional(conditions, params, "tech = :tech", "tech", tech)
    _optional(
        conditions, params, "segment_status = :segment_status", "segment_status", segment_status
    )
    _optional(
        conditions,
        params,
        "(doc_no LIKE :search OR customer_name LIKE :search OR description LIKE :search "
        "OR unit_model LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    {_segment_lines_cte()}
    SELECT date(activity_date)      AS activity_date,
           doc_no,
           customer_name,
           tech,
           wo_status,
           CASE WHEN bill_as = 'flatrate' THEN 'Flat rate' ELSE 'Actual hours' END AS bill_as,
           segment_status,
           description,
           unit_model,
           actual_hrs,
           flat_rate_hrs,
           labor_rate,
           shop_fee,
           meter,
           revenue,
           CASE WHEN actual_hrs > 0 THEN revenue / actual_hrs END AS effective_rate
    FROM segments
    {_where(conditions)}
    """
    return _paged(
        "service_detail",
        body,
        _SERVICE_DETAIL_SORTS,
        "activity_date",
        params,
        page,
        page_size,
        sort,
        direction,
    )


_SERVICE_WIP_SORTS = {
    "activity_date": "activity_date",
    "doc_no": "doc_no",
    "doc_status": "doc_status",
    "wo_status": "wo_status",
    "tech": "tech",
    "customer_name": "customer_name",
    "unit_model": "unit_model",
    "open_segments": "open_segments",
    "actual_hrs": "actual_hrs",
    "open_value": "open_value",
    "age_days": "age_days",
}


def service_wip(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    bucket: str | None = None,
    wo_status: str | None = None,
    doc_status: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = {"as_of": end, "as_of_end": f"{end} 23:59:59"}
    _optional(conditions, params, f"{_WIP_AGE_CASE} = :bucket", "bucket", bucket)
    _optional(conditions, params, "wo_status = :wo_status", "wo_status", wo_status)
    _optional(conditions, params, "doc_status = :doc_status", "doc_status", doc_status)
    _optional(
        conditions,
        params,
        "(doc_no LIKE :search OR customer_name LIKE :search OR unit_model LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    SELECT *, {_WIP_AGE_CASE} AS bucket
    FROM ({_wip_body()})
    {_where(conditions)}
    """
    return _paged(
        "service_wip",
        body,
        _SERVICE_WIP_SORTS,
        "age_days",
        params,
        page,
        page_size,
        sort,
        direction,
    )


# ====================================================================== RENTAL


# Rental has no cost basis either: RentalUnit.DepreciationAmt is 0 on all 22,995
# rows and every RentalGroup.DepreciationPct is 0, so no margin can be computed.
RENTAL_HAS_COST_BASIS = False

# Billed duration normalised to days. ReturnDate is deliberately unused: it is
# NULL on 19,930 of 22,995 rental lines.
_RENTAL_BILLED_DAYS = """
    CASE TRIM(ru.RentalDuration)
        WHEN 'hour' THEN ru.DurationQty / 24.0
        WHEN 'day'  THEN ru.DurationQty
        WHEN 'week' THEN ru.DurationQty * 7
        WHEN 'mon'  THEN ru.DurationQty * 30
        ELSE ru.DurationQty
    END
"""

# Calendar span between the booked start and end. Across full history this
# totals 126,170 days against 65,642 billed days, so the two are reported side
# by side rather than one standing in for the other.
_RENTAL_SPAN_DAYS = "MAX(julianday(ru.EndDate) - julianday(ru.StartDate), 0)"

IDLE_BUCKETS = ["on rent in window", "idle < 90 days", "idle 90-365 days", "idle 365+ days", "never rented"]

_IDLE_CASE = """
    CASE
        WHEN rentals_in_window > 0 THEN 'on rent in window'
        WHEN last_rented IS NULL   THEN 'never rented'
        WHEN idle_days < 90        THEN 'idle < 90 days'
        WHEN idle_days < 366       THEN 'idle 90-365 days'
        ELSE 'idle 365+ days'
    END
"""


def _rental_lines_cte() -> str:
    """Booked rental lines with their unit, group and contract context.

    RentalContract is one row per invoice so the join cannot fan out. Every
    rental line resolves to a UnitBase row, but only the 126 units flagged
    Rental=1 carry a RentalGroupId, which is why group coverage is partial.
    """
    return f"""
    WITH rental_lines AS (
        SELECT
            h.InvoiceDocId                              AS invoice_doc_id,
            h.DocNo                                     AS doc_no,
            h.ActivityDate                              AS activity_date,
            h.CustomerId                                AS customer_id,
            h.CustomerName                              AS customer_name,
            ru.RentalUnitId                             AS rental_unit_id,
            ru.UnitId                                   AS unit_id,
            TRIM(ru.StockNo)                            AS stock_no,
            COALESCE(NULLIF(TRIM(ru.Model), ''), NULLIF(TRIM(ub.Model), ''),
                     '(unspecified)')                   AS model,
            COALESCE(rg.DisplayText, 'Ungrouped')       AS rental_group,
            TRIM(ru.RentalDuration)                     AS duration_unit,
            ru.DurationQty                              AS duration_qty,
            {_RENTAL_BILLED_DAYS}                       AS billed_days,
            {_RENTAL_SPAN_DAYS}                         AS span_days,
            date(ru.StartDate)                          AS start_date,
            date(ru.EndDate)                            AS end_date,
            date(ru.ReturnDate)                         AS return_date,
            ru.IsReturned                               AS is_returned,
            ru.RateAmtPerDuration                       AS rate,
            COALESCE(ru.DiscountAmt, 0)                 AS discount_amt,
            COALESCE(ru.MinChargeAmt, 0)                AS min_charge,
            COALESCE(ru.MeterBegin, 0)                  AS meter_begin,
            COALESCE(ru.MeterEnd, 0)                    AS meter_end,
            MAX(COALESCE(ru.MeterEnd, 0) - COALESCE(ru.MeterBegin, 0), 0) AS meter_used,
            COALESCE(ru.MeterRate, 0)                   AS meter_rate,
            COALESCE(ru.OverageRate, 0)                 AS overage_rate,
            COALESCE(rc.ContractNo, '')                 AS contract_no,
            COALESCE(rc.ContractStatus, 'none')         AS contract_status,
            COALESCE(rc.TransactionType, 'none')        AS transaction_type,
            d.NetExt                                    AS revenue
        FROM InvoiceHeader h
        JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId AND d.ItemType = 'RU'
        JOIN RentalUnit ru ON ru.ItemId = d.ItemId
        LEFT JOIN UnitBase ub ON ub.UnitId = ru.UnitId
        LEFT JOIN RentalGroup rg ON rg.RentalGroupId = ub.RentalGroupId
        LEFT JOIN RentalContract rc ON rc.InvoiceDocId = h.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start AND h.ActivityDate <= :end
    )
    """


def _rental_fleet_body() -> str:
    """Every unit that has ever rented, with its activity inside the window.

    The fleet is defined by rental history rather than UnitBase.Rental, because
    680 distinct units have rental lines against them while only 126 carry the
    flag. Both counts are reported so the gap is visible instead of hidden in a
    denominator.
    """
    return f"""
    SELECT
        ub.UnitId                                       AS unit_id,
        TRIM(ub.StockNo)                                AS stock_no,
        COALESCE(NULLIF(TRIM(ub.Make), ''), 'Unspecified')   AS make,
        COALESCE(NULLIF(TRIM(ub.Model), ''), '(unspecified)') AS model,
        ub.Year                                         AS model_year,
        COALESCE(rg.DisplayText, 'Ungrouped')           AS rental_group,
        ub.Rental                                       AS flagged_rental,
        TRIM(ub.StockStatus)                            AS stock_status,
        hist.last_rented                                AS last_rented,
        COALESCE(hist.rentals, 0)                       AS rentals_all_time,
        COALESCE(hist.revenue, 0)                       AS revenue_all_time,
        COALESCE(win.rentals, 0)                        AS rentals_in_window,
        COALESCE(win.revenue, 0)                        AS revenue_in_window,
        COALESCE(win.billed_days, 0)                    AS billed_days,
        COALESCE(win.meter_used, 0)                     AS meter_used,
        CAST(julianday(:as_of) - julianday(hist.last_rented) AS INTEGER) AS idle_days
    FROM UnitBase ub
    LEFT JOIN RentalGroup rg ON rg.RentalGroupId = ub.RentalGroupId
    LEFT JOIN (
        SELECT ru.UnitId                    AS unit_id,
               MAX(date(h.ActivityDate))    AS last_rented,
               COUNT(*)                     AS rentals,
               SUM(d.NetExt)                AS revenue
        FROM RentalUnit ru
        JOIN InvoiceDetail d ON d.ItemId = ru.ItemId AND d.ItemType = 'RU'
        JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES} AND h.ActivityDate <= :as_of_end
        GROUP BY ru.UnitId
    ) hist ON hist.unit_id = ub.UnitId
    LEFT JOIN (
        SELECT ru.UnitId                    AS unit_id,
               COUNT(*)                     AS rentals,
               SUM(d.NetExt)                AS revenue,
               SUM({_RENTAL_BILLED_DAYS})   AS billed_days,
               SUM(MAX(COALESCE(ru.MeterEnd, 0) - COALESCE(ru.MeterBegin, 0), 0)) AS meter_used
        FROM RentalUnit ru
        JOIN InvoiceDetail d ON d.ItemId = ru.ItemId AND d.ItemType = 'RU'
        JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start AND h.ActivityDate <= :end
        GROUP BY ru.UnitId
    ) win ON win.unit_id = ub.UnitId
    WHERE ub.Rental = 1 OR hist.unit_id IS NOT NULL
    """


def rental_summary(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)

        totals = (
            db.query_one(
                f"""
                {_rental_lines_cte()}
                SELECT COUNT(*)                                 AS lines,
                       COUNT(DISTINCT unit_id)                  AS units_rented,
                       COUNT(DISTINCT invoice_doc_id)           AS invoices,
                       COUNT(DISTINCT customer_id)              AS customers,
                       COUNT(DISTINCT NULLIF(contract_no, ''))  AS contracts,
                       COALESCE(SUM(revenue), 0)                AS revenue,
                       COALESCE(SUM(billed_days), 0)            AS billed_days,
                       COALESCE(SUM(span_days), 0)              AS span_days,
                       COALESCE(SUM(discount_amt), 0)           AS discount,
                       COALESCE(SUM(meter_used), 0)             AS meter_used,
                       SUM(CASE WHEN meter_used > 0 THEN 1 ELSE 0 END)     AS lines_with_meter,
                       SUM(CASE WHEN return_date IS NULL THEN 1 ELSE 0 END) AS missing_return,
                       SUM(CASE WHEN is_returned = 1 THEN 1 ELSE 0 END)     AS flagged_returned
                FROM rental_lines
                """,
                bounds,
            )
            or {}
        )

        fleet = (
            db.query_one(
                """
                SELECT COUNT(*) AS flagged_fleet,
                       SUM(CASE WHEN IsActive = 1 THEN 1 ELSE 0 END) AS flagged_active
                FROM UnitBase WHERE Rental = 1
                """
            )
            or {}
        )

        ever = (
            db.query_one(
                f"""
                SELECT COUNT(*) AS units,
                       SUM(CASE WHEN rentals_in_window > 0 THEN 1 ELSE 0 END) AS active_in_window,
                       SUM(CASE WHEN rentals_in_window = 0 AND last_rented IS NOT NULL
                                THEN 1 ELSE 0 END) AS idle_in_window
                FROM ({_rental_fleet_body()})
                """,
                {**bounds, "as_of": end, "as_of_end": f"{end} 23:59:59"},
            )
            or {}
        )

        revenue = totals.get("revenue") or 0.0
        lines = totals.get("lines") or 0
        units = totals.get("units_rented") or 0
        billed_days = totals.get("billed_days") or 0.0
        meter = totals.get("meter_used") or 0.0
        fleet_ever = ever.get("units") or 0

        return {
            "revenue": revenue,
            "lines": lines,
            "invoices": totals.get("invoices") or 0,
            "customers": totals.get("customers") or 0,
            "contracts": totals.get("contracts") or 0,
            "units_rented": units,
            "revenue_per_unit": (revenue / units) if units else None,
            "turns_per_unit": (lines / units) if units else None,
            "billed_days": billed_days,
            "span_days": totals.get("span_days") or 0.0,
            "avg_billed_days": (billed_days / lines) if lines else None,
            "revenue_per_day": (revenue / billed_days) if billed_days else None,
            "discount": totals.get("discount") or 0.0,
            "meter_used": meter,
            "lines_with_meter": totals.get("lines_with_meter") or 0,
            "revenue_per_meter_hour": (revenue / meter) if meter else None,
            "missing_return": totals.get("missing_return") or 0,
            "flagged_returned": totals.get("flagged_returned") or 0,
            "flagged_fleet": fleet.get("flagged_fleet") or 0,
            "flagged_active": fleet.get("flagged_active") or 0,
            "fleet_ever_rented": fleet_ever,
            "active_in_window": ever.get("active_in_window") or 0,
            "idle_in_window": ever.get("idle_in_window") or 0,
            # Against every unit with rental history, not the stale Rental flag.
            "utilisation_pct": (
                (ever.get("active_in_window") or 0) / fleet_ever * 100 if fleet_ever else None
            ),
            "has_cost_basis": RENTAL_HAS_COST_BASIS,
            "as_of": end,
        }

    return db.cached("rental_summary", [start, end], build)


def rental_charts(start: str, end: str, grain: str = "month") -> dict[str, Any]:
    grain = grain if grain in ("month", "quarter", "year") else "month"

    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)
        cte = _rental_lines_cte()
        period = _period_expr(grain, "activity_date")

        utilisation = db.query(
            f"""
            {cte}
            SELECT {period}                             AS period,
                   COUNT(*)                             AS lines,
                   COUNT(DISTINCT unit_id)              AS units_on_rent,
                   COALESCE(SUM(revenue), 0)            AS revenue,
                   COALESCE(SUM(billed_days), 0)        AS billed_days,
                   COALESCE(SUM(meter_used), 0)         AS meter_used
            FROM rental_lines
            GROUP BY 1
            ORDER BY 1
            """,
            bounds,
        )
        for r in utilisation:
            r["revenue_per_unit"] = (
                (r["revenue"] / r["units_on_rent"]) if r["units_on_rent"] else None
            )
            r["revenue_per_day"] = (
                (r["revenue"] / r["billed_days"]) if r["billed_days"] else None
            )

        def dimension(expr: str, alias: str, limit: int | None = None) -> list[dict[str, Any]]:
            rows = db.query(
                f"""
                {cte}
                SELECT {expr}                           AS {alias},
                       COUNT(*)                         AS lines,
                       COUNT(DISTINCT unit_id)          AS units,
                       COALESCE(SUM(revenue), 0)        AS revenue,
                       COALESCE(SUM(billed_days), 0)    AS billed_days
                FROM rental_lines
                GROUP BY 1
                ORDER BY revenue DESC
                {f'LIMIT {limit}' if limit else ''}
                """,
                bounds,
            )
            for r in rows:
                r["revenue_per_day"] = (
                    (r["revenue"] / r["billed_days"]) if r["billed_days"] else None
                )
                r["revenue_per_unit"] = (r["revenue"] / r["units"]) if r["units"] else None
            return rows

        top_units = db.query(
            f"""
            {cte}
            SELECT stock_no,
                   MAX(model)                       AS model,
                   MAX(rental_group)                AS rental_group,
                   COUNT(*)                         AS rentals,
                   COALESCE(SUM(revenue), 0)        AS revenue,
                   COALESCE(SUM(billed_days), 0)    AS billed_days,
                   COALESCE(SUM(meter_used), 0)     AS meter_used
            FROM rental_lines
            GROUP BY stock_no
            ORDER BY revenue DESC
            LIMIT 15
            """,
            bounds,
        )
        for r in top_units:
            r["revenue_per_day"] = (
                (r["revenue"] / r["billed_days"]) if r["billed_days"] else None
            )

        snapshot = {**bounds, "as_of": end, "as_of_end": f"{end} 23:59:59"}
        idle_rows = db.query(
            f"""
            SELECT {_IDLE_CASE}                     AS bucket,
                   COUNT(*)                         AS units,
                   COALESCE(SUM(revenue_all_time), 0) AS revenue_all_time,
                   SUM(CASE WHEN flagged_rental = 1 THEN 1 ELSE 0 END) AS flagged
            FROM ({_rental_fleet_body()})
            GROUP BY 1
            """,
            snapshot,
        )
        by_bucket = {r["bucket"]: r for r in idle_rows}
        idle = [
            by_bucket.get(
                b, {"bucket": b, "units": 0, "revenue_all_time": 0.0, "flagged": 0}
            )
            for b in IDLE_BUCKETS
        ]

        return {
            "grain": grain,
            "utilisation": utilisation,
            "groups": dimension("rental_group", "rental_group"),
            "durations": dimension("duration_unit", "duration_unit"),
            "contract_status": dimension("contract_status", "contract_status"),
            "top_units": top_units,
            "idle": idle,
            "idle_buckets": IDLE_BUCKETS,
            "has_cost_basis": RENTAL_HAS_COST_BASIS,
        }

    return db.cached("rental_charts", [start, end, grain], build)


_RENTAL_DETAIL_SORTS = {
    "activity_date": "activity_date",
    "doc_no": "doc_no",
    "stock_no": "stock_no",
    "model": "model",
    "rental_group": "rental_group",
    "customer_name": "customer_name",
    "contract_status": "contract_status",
    "duration_unit": "duration_unit",
    "billed_days": "billed_days",
    "span_days": "span_days",
    "start_date": "start_date",
    "end_date": "end_date",
    "rate": "rate",
    "meter_used": "meter_used",
    "revenue": "revenue",
}


def rental_detail(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    rental_group: str | None = None,
    duration_unit: str | None = None,
    contract_status: str | None = None,
    stock_no: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = dict(_bounds(start, end))
    _optional(conditions, params, "rental_group = :rental_group", "rental_group", rental_group)
    _optional(conditions, params, "duration_unit = :duration_unit", "duration_unit", duration_unit)
    _optional(
        conditions, params, "contract_status = :contract_status", "contract_status", contract_status
    )
    _optional(conditions, params, "stock_no = :stock_no", "stock_no", stock_no)
    _optional(
        conditions,
        params,
        "(stock_no LIKE :search OR model LIKE :search OR customer_name LIKE :search "
        "OR contract_no LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    {_rental_lines_cte()}
    SELECT date(activity_date)      AS activity_date,
           doc_no,
           stock_no,
           model,
           rental_group,
           customer_name,
           contract_no,
           contract_status,
           duration_unit,
           duration_qty,
           billed_days,
           span_days,
           start_date,
           end_date,
           return_date,
           rate,
           discount_amt,
           meter_begin,
           meter_end,
           meter_used,
           revenue
    FROM rental_lines
    {_where(conditions)}
    """
    return _paged(
        "rental_detail",
        body,
        _RENTAL_DETAIL_SORTS,
        "revenue",
        params,
        page,
        page_size,
        sort,
        direction,
    )


_RENTAL_FLEET_SORTS = {
    "stock_no": "stock_no",
    "make": "make",
    "model": "model",
    "model_year": "model_year",
    "rental_group": "rental_group",
    "stock_status": "stock_status",
    "last_rented": "last_rented",
    "idle_days": "idle_days",
    "rentals_all_time": "rentals_all_time",
    "revenue_all_time": "revenue_all_time",
    "rentals_in_window": "rentals_in_window",
    "revenue_in_window": "revenue_in_window",
    "billed_days": "billed_days",
    "meter_used": "meter_used",
}


def rental_fleet(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    bucket: str | None = None,
    rental_group: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: dict[str, Any] = {
        **_bounds(start, end),
        "as_of": end,
        "as_of_end": f"{end} 23:59:59",
    }
    _optional(conditions, params, f"{_IDLE_CASE} = :bucket", "bucket", bucket)
    _optional(conditions, params, "rental_group = :rental_group", "rental_group", rental_group)
    _optional(
        conditions,
        params,
        "(stock_no LIKE :search OR model LIKE :search OR make LIKE :search)",
        "search",
        f"%{search}%" if search else None,
    )

    body = f"""
    SELECT *, {_IDLE_CASE} AS bucket
    FROM ({_rental_fleet_body()})
    {_where(conditions)}
    """
    return _paged(
        "rental_fleet",
        body,
        _RENTAL_FLEET_SORTS,
        "revenue_in_window",
        params,
        page,
        page_size,
        sort,
        direction,
    )


# =================================================================== CUSTOMERS


CUSTOMER_SEGMENTS = ["New", "Active", "At risk", "Churned", "Never purchased"]

CREDIT_BANDS = ["none", "under $5k", "$5k-$10k", "$10k-$25k", "$25k-$50k", "$50k+"]


def _customer_segment(row: dict[str, Any], new_since: str) -> str:
    """Where an account sits, by how recently it last bought.

    Recency runs from the end of the window rather than today, so the filter bar
    moves the classification with it instead of always describing the present.

    The bands are fixed at 180 and 365 days rather than 'bought inside the
    window', which would make At risk structurally empty on any window of a year
    or more. `new_since` is likewise capped at a year before the window end, or
    every account would count as new once the window covers all history. A new
    account that has already gone quiet is reported as at risk, which is the
    more useful thing to know about it.
    """
    if row["last_purchase"] is None:
        return "Never purchased"
    recency = row["recency_days"] or 0
    if recency > 365:
        return "Churned"
    if recency > 180:
        return "At risk"
    if row["invoices_in_window"] and (row["first_purchase"] or "") >= new_since:
        return "New"
    return "Active"


def _credit_band(limit: float | None) -> str:
    limit = limit or 0.0
    if limit <= 0:
        return "none"
    if limit < 5_000:
        return "under $5k"
    if limit < 10_000:
        return "$5k-$10k"
    if limit < 25_000:
        return "$10k-$25k"
    if limit < 50_000:
        return "$25k-$50k"
    return "$50k+"


def _customer_body() -> str:
    """One row per customer, with window activity and all-time history.

    CustomerAddress holds up to 5 rows per customer and CustomerClass up to 11,
    so both are read through a single-row subquery: joining them would multiply
    the revenue attached to a customer.

    The receivable balance is a proxy. The schema has no AR ledger; it nets the
    'recv' rows Payment writes when an invoice goes on account against the
    'recvpmt' rows written when it is settled. Across full history that nets to
    $9.4M, which is plausible but is not an aged trial balance.
    """
    return f"""
    SELECT
        c.CustomerId                                    AS customer_id,
        TRIM(c.CustomerNo)                              AS customer_no,
        c.CustomerName                                  AS customer_name,
        c.IsBusiness                                    AS is_business,
        c.CredLimit                                     AS credit_limit,
        c.CreditHoldFlag                                AS credit_hold,
        c.IsActive                                      AS is_active,
        date(c.EntDate)                                 AS entered,
        (SELECT COALESCE(NULLIF(TRIM(a.City), ''), '(unknown)') FROM CustomerAddress a
          WHERE a.CustomerId = c.CustomerId
          ORDER BY a.IsDefault DESC, a.AddressId LIMIT 1)            AS city,
        (SELECT COALESCE(NULLIF(TRIM(a.State), ''), '??') FROM CustomerAddress a
          WHERE a.CustomerId = c.CustomerId
          ORDER BY a.IsDefault DESC, a.AddressId LIMIT 1)            AS state,
        (SELECT ct.DisplayText FROM CustomerClass cc
          JOIN CustomerClassType ct ON ct.ClassTypeId = cc.ClassTypeId
          WHERE cc.CustomerId = c.CustomerId ORDER BY cc.ClassId LIMIT 1) AS class,
        (SELECT COALESCE(NULLIF(TRIM(u.FirstName || ' ' || u.LastName), ''), u.UserName)
          FROM AppUser u WHERE u.AppUserId = c.DefSalespersonId)      AS salesperson,
        hist.first_purchase                             AS first_purchase,
        hist.last_purchase                              AS last_purchase,
        COALESCE(hist.revenue, 0)                       AS revenue_all_time,
        COALESCE(hist.invoices, 0)                      AS invoices_all_time,
        COALESCE(win.revenue, 0)                        AS revenue_in_window,
        COALESCE(win.gross_profit, 0)                   AS gross_profit_in_window,
        COALESCE(win.invoices, 0)                       AS invoices_in_window,
        COALESCE(ar.balance, 0)                         AS ar_balance,
        CAST(julianday(:as_of) - julianday(hist.last_purchase) AS INTEGER) AS recency_days
    FROM Customer c
    LEFT JOIN (
        SELECT h.CustomerId                 AS customer_id,
               MIN(date(h.ActivityDate))    AS first_purchase,
               MAX(date(h.ActivityDate))    AS last_purchase,
               SUM(d.NetExt)                AS revenue,
               COUNT(DISTINCT h.InvoiceDocId) AS invoices
        FROM InvoiceHeader h
        JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate <= :as_of_end
          AND d.ItemType NOT IN {NON_REVENUE_ITEM_TYPES}
        GROUP BY h.CustomerId
    ) hist ON hist.customer_id = c.CustomerId
    LEFT JOIN (
        SELECT h.CustomerId                 AS customer_id,
               SUM(d.NetExt)                AS revenue,
               SUM(CASE WHEN d.ItemType IN {COSTED_ITEM_TYPES}
                        THEN d.NetExt - ({_cost_case()}) ELSE 0 END) AS gross_profit,
               COUNT(DISTINCT h.InvoiceDocId) AS invoices
        FROM InvoiceHeader h
        JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId
        WHERE h.Status IN {BOOKED_STATUSES}
          AND h.ActivityDate >= :start AND h.ActivityDate <= :end
          AND d.ItemType NOT IN {NON_REVENUE_ITEM_TYPES}
        GROUP BY h.CustomerId
    ) win ON win.customer_id = c.CustomerId
    LEFT JOIN (
        SELECT h.CustomerId AS customer_id, SUM(p.Amount) AS balance
        FROM Payment p
        JOIN InvoiceHeader h ON h.InvoiceDocId = p.InvoiceDocId
        WHERE p.IsActive = 1 AND p.PmtType IN ('recv', 'recvpmt')
          AND h.ActivityDate <= :as_of_end
        GROUP BY h.CustomerId
    ) ar ON ar.customer_id = c.CustomerId
    """


def _customer_rows(start: str, end: str) -> list[dict[str, Any]]:
    """One row per customer, materialised once per window.

    The query behind this costs about four seconds because it aggregates the
    whole invoice detail table twice, once for history and once for the window.
    All three customer panels read the same 5,967 rows instead of paying for it
    each time.
    """

    def build() -> list[dict[str, Any]]:
        rows = db.query(
            _customer_body(),
            {**_bounds(start, end), "as_of": end, "as_of_end": f"{end} 23:59:59"},
        )
        new_since = max(start, (date.fromisoformat(end) - timedelta(days=365)).isoformat())
        for r in rows:
            r["segment"] = _customer_segment(r, new_since)
            r["credit_band"] = _credit_band(r["credit_limit"])
            r["class"] = r["class"] or "Unclassified"
        return rows

    return db.cached("customer_rows", [start, end], build)


def customers_summary(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        rows = _customer_rows(start, end)

        active = [r for r in rows if r["invoices_in_window"]]
        revenue = sum(r["revenue_in_window"] for r in active)
        by_segment: dict[str, int] = {}
        for r in rows:
            by_segment[r["segment"]] = by_segment.get(r["segment"], 0) + 1

        top10 = sum(
            sorted((r["revenue_in_window"] for r in active), reverse=True)[:10]
        )

        terms = (
            db.query_one(
                """
                SELECT COALESCE(AVG(NULLIF(DaysDue, 0)), 0) AS avg_days_due,
                       COUNT(DISTINCT BillToCustomerId)     AS accounts
                FROM PaymentReceivablesDetail
                """
            )
            or {}
        )

        return {
            "customers_on_file": len(rows),
            # Transacted in the window, whatever the invoice was worth; the
            # segment counts below are recency bands and answer a different
            # question, so the two do not add up to each other.
            "active": len(active),
            "recently_active": by_segment.get("Active", 0) + by_segment.get("New", 0),
            "new_customers": by_segment.get("New", 0),
            "at_risk": by_segment.get("At risk", 0),
            "churned": by_segment.get("Churned", 0),
            "never_purchased": by_segment.get("Never purchased", 0),
            "revenue": revenue,
            "gross_profit": sum(r["gross_profit_in_window"] for r in rows),
            "revenue_per_customer": (revenue / len(active)) if active else None,
            "top10_revenue": top10,
            "top10_share_pct": (top10 / revenue * 100) if revenue else None,
            "credit_limit": sum(r["credit_limit"] or 0.0 for r in rows),
            "on_hold": sum(1 for r in rows if r["credit_hold"] == 1),
            "ar_balance": sum(r["ar_balance"] or 0.0 for r in rows),
            "over_limit": sum(
                1
                for r in rows
                if (r["ar_balance"] or 0) > 0
                and (r["credit_limit"] or 0) > 0
                and r["ar_balance"] > r["credit_limit"]
            ),
            "avg_days_due": terms.get("avg_days_due") or 0.0,
            "receivable_accounts": terms.get("accounts") or 0,
            "as_of": end,
        }

    return db.cached("customers_summary", [start, end], build)


def customers_charts(start: str, end: str) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        bounds = _bounds(start, end)
        rows = _customer_rows(start, end)

        def rollup(field: str, order: list[str] | None = None, limit: int | None = None):
            acc: dict[str, dict[str, Any]] = {}
            for src in rows:
                bucket = acc.setdefault(
                    src[field],
                    {
                        field: src[field],
                        "customers": 0,
                        "active": 0,
                        "revenue": 0.0,
                        "gross_profit": 0.0,
                        "credit_limit": 0.0,
                        "ar_balance": 0.0,
                    },
                )
                bucket["customers"] += 1
                bucket["active"] += 1 if src["invoices_in_window"] else 0
                bucket["revenue"] += src["revenue_in_window"] or 0.0
                bucket["gross_profit"] += src["gross_profit_in_window"] or 0.0
                bucket["credit_limit"] += src["credit_limit"] or 0.0
                bucket["ar_balance"] += src["ar_balance"] or 0.0
            # No margin is derived here: gross profit covers Units and Parts
            # only while revenue covers every department, so the ratio of the
            # two would not be a margin.
            if order:
                for key in order:
                    acc.setdefault(
                        key,
                        {
                            field: key,
                            "customers": 0,
                            "active": 0,
                            "revenue": 0.0,
                            "gross_profit": 0.0,
                            "credit_limit": 0.0,
                            "ar_balance": 0.0,
                        },
                    )
                return [acc[k] for k in order]
            ranked = sorted(acc.values(), key=lambda r: r["revenue"], reverse=True)
            return ranked[:limit] if limit else ranked

        def slim(src: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
            return {f: src[f] for f in fields}

        top = [
            slim(
                r,
                (
                    "customer_no",
                    "customer_name",
                    "city",
                    "state",
                    "class",
                    "segment",
                    "revenue_in_window",
                    "gross_profit_in_window",
                    "invoices_in_window",
                    "revenue_all_time",
                    "last_purchase",
                    "recency_days",
                    "ar_balance",
                    "credit_limit",
                ),
            )
            for r in sorted(
                (r for r in rows if r["revenue_in_window"]),
                key=lambda r: r["revenue_in_window"],
                reverse=True,
            )[:15]
        ]

        # Accounts with real history that bought nothing inside the window.
        at_risk = [
            slim(
                r,
                (
                    "customer_no",
                    "customer_name",
                    "city",
                    "state",
                    "class",
                    "segment",
                    "revenue_all_time",
                    "invoices_all_time",
                    "last_purchase",
                    "recency_days",
                    "ar_balance",
                    "credit_limit",
                ),
            )
            for r in sorted(
                (r for r in rows if not r["invoices_in_window"] and r["last_purchase"]),
                key=lambda r: r["revenue_all_time"],
                reverse=True,
            )[:15]
        ]

        # Geography comes from the invoice's own billing address rather than the
        # customer record: CustomerAddress.IsDefault is set on only 556 of 6,838
        # rows, so the invoice is the more reliable source of where revenue came
        # from.
        states = db.query(
            f"""
            {_lines_cte()}
            SELECT COALESCE(NULLIF(TRIM(h.BillingState), ''), '??') AS state,
                   COUNT(DISTINCT l.InvoiceDocId)                   AS invoices,
                   COUNT(DISTINCT l.CustomerId)                     AS customers,
                   COALESCE(SUM(l.revenue), 0)                      AS revenue
            FROM lines l
            JOIN InvoiceHeader h ON h.InvoiceDocId = l.InvoiceDocId
            WHERE l.department NOT IN {NON_REVENUE_DEPARTMENTS}
            GROUP BY 1
            ORDER BY revenue DESC
            LIMIT 12
            """,
            bounds,
        )

        cities = db.query(
            f"""
            {_lines_cte()}
            SELECT COALESCE(NULLIF(TRIM(h.BillingCity), ''), '(unknown)') || ', ' ||
                   COALESCE(NULLIF(TRIM(h.BillingState), ''), '??')  AS city,
                   COUNT(DISTINCT l.CustomerId)                      AS customers,
                   COALESCE(SUM(l.revenue), 0)                       AS revenue
            FROM lines l
            JOIN InvoiceHeader h ON h.InvoiceDocId = l.InvoiceDocId
            WHERE l.department NOT IN {NON_REVENUE_DEPARTMENTS}
            GROUP BY 1
            ORDER BY revenue DESC
            LIMIT 14
            """,
            bounds,
        )

        payments = db.query(
            f"""
            SELECT COALESCE(pm.DisplayText, 'Unknown')  AS method,
                   COALESCE(pm.PayType, 'unknown')      AS pay_type,
                   COUNT(*)                             AS payments,
                   COALESCE(SUM(p.Amount), 0)           AS amount
            FROM Payment p
            JOIN InvoiceHeader h ON h.InvoiceDocId = p.InvoiceDocId
            LEFT JOIN PaymentMethod pm ON pm.PaymentMethodId = p.PaymentMethodId
            WHERE p.IsActive = 1 AND h.Status IN {BOOKED_STATUSES}
              AND h.ActivityDate >= :start AND h.ActivityDate <= :end
            GROUP BY 1, 2
            ORDER BY amount DESC
            LIMIT 12
            """,
            bounds,
        )

        # Retention is measured by year regardless of the trend grain, since a
        # cohort only means something over years. 'New' is judged against each
        # customer's first ever purchase, not the first one inside the window.
        activity = db.query(
            f"""
            {_lines_cte()}
            SELECT substr(ActivityDate, 1, 4)   AS yr,
                   CustomerId                   AS customer_id,
                   SUM(revenue)                 AS revenue
            FROM lines
            WHERE department NOT IN {NON_REVENUE_DEPARTMENTS}
            GROUP BY 1, 2
            """,
            bounds,
        )
        first_year = {
            r["customer_id"]: r["yr"]
            for r in db.query(
                f"""
                {_lines_cte()}
                SELECT CustomerId AS customer_id, MIN(substr(ActivityDate, 1, 4)) AS yr
                FROM lines
                WHERE department NOT IN {NON_REVENUE_DEPARTMENTS}
                GROUP BY 1
                """,
                _bounds("2000-01-01", end),
            )
        }

        by_year: dict[str, set[int]] = {}
        revenue_by_year: dict[str, float] = {}
        for r in activity:
            by_year.setdefault(r["yr"], set()).add(r["customer_id"])
            revenue_by_year[r["yr"]] = revenue_by_year.get(r["yr"], 0.0) + (r["revenue"] or 0.0)

        retention = []
        previous: set[int] | None = None
        for yr in sorted(by_year):
            current = by_year[yr]
            new = {cid for cid in current if first_year.get(cid) == yr}
            row = {
                "year": yr,
                "active": len(current),
                "new": len(new),
                "returning": len(current) - len(new),
                "revenue": revenue_by_year.get(yr, 0.0),
                "churned": len(previous - current) if previous is not None else None,
                "retained_pct": (
                    len(previous & current) / len(previous) * 100
                    if previous
                    else None
                ),
            }
            retention.append(row)
            previous = current

        return {
            "segments": rollup("segment", CUSTOMER_SEGMENTS),
            "segment_names": CUSTOMER_SEGMENTS,
            "credit": rollup("credit_band", CREDIT_BANDS),
            "credit_bands": CREDIT_BANDS,
            "classes": rollup("class", limit=12),
            "top": top,
            "at_risk": at_risk,
            "states": states,
            "cities": cities,
            "payments": payments,
            "retention": retention,
        }

    return db.cached("customers_charts", [start, end], build)


_CUSTOMER_SORTS = (
    "customer_no",
    "customer_name",
    "city",
    "state",
    "class",
    "salesperson",
    "segment",
    "credit_band",
    "revenue_in_window",
    "gross_profit_in_window",
    "invoices_in_window",
    "revenue_all_time",
    "invoices_all_time",
    "first_purchase",
    "last_purchase",
    "recency_days",
    "credit_limit",
    "ar_balance",
)


def customers_detail(
    start: str,
    end: str,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = None,
    direction: str = "desc",
    segment: str | None = None,
    state: str | None = None,
    customer_class: str | None = None,
    band: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    rows = _customer_rows(start, end)
    needle = (search or "").lower()

    def keep(r: dict[str, Any]) -> bool:
        if segment and r["segment"] != segment:
            return False
        if state and r["state"] != state:
            return False
        if customer_class and r["class"] != customer_class:
            return False
        if band and r["credit_band"] != band:
            return False
        if needle and needle not in (
            f"{r['customer_name']} {r['customer_no']} {r['city']}".lower()
        ):
            return False
        return True

    return _page_rows(
        [r for r in rows if keep(r)],
        _CUSTOMER_SORTS,
        "revenue_in_window",
        page,
        page_size,
        sort,
        direction,
    )
