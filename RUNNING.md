# Running the Perseus BI Dashboard

A FastAPI backend serving a read-only semantic layer over the Perseus SQLite
database, plus a React dashboard in `app/static/`. There is no bundler: the
browser loads React, ReactDOM and ECharts as plain `<script>` tags and Babel
standalone transpiles the JSX in the page.

## Prerequisites

- Python 3.10+
- Node.js 18+ (only to fetch the browser libraries; nothing is compiled)

## 1. Install the Python dependencies

```bash
pip install -r requirements.txt
```

## 2. Install the frontend libraries

```bash
npm install
```

`npm install` runs `scripts/vendor.js` via `postinstall`, which copies the
pinned browser builds of React, ReactDOM, ECharts and Babel standalone out of
`node_modules` into `app/static/vendor/`. That directory is a build output and
is not tracked in git, so a fresh clone needs this step before the page will
load. To refresh it without reinstalling, run `npm run vendor`.

## 3. Provide the database

The database is a ~695 MB artifact and is not committed (`.gitignore` excludes
`*.db`). It is shared separately. Put your copy at the repository root:

```
perseus-equipment-hackathon/perseus_equipment_database.db
```

The default path is resolved relative to the source files, not the working
directory, so the server behaves the same whatever directory you start it from.

To keep the database elsewhere, point `PERSEUS_DB` at the full path instead:

```powershell
$env:PERSEUS_DB = "D:\data\perseus_equipment_database.db"   # PowerShell
```

```bash
export PERSEUS_DB=/data/perseus_equipment_database.db        # bash
```

If the file cannot be found, the server fails with an error naming both the
path it expected and the `PERSEUS_DB` override.

Every connection is opened read-only (`mode=ro` plus `PRAGMA query_only = ON`),
so no code path in this app can modify the database.

## 4. Start the server

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On Windows, `./run.ps1` does the same thing. Then open
<http://127.0.0.1:8000>.

At startup a background thread warms the cache for the two date presets the UI
opens with. Until it finishes, the first request to an endpoint runs its
aggregation live: `/api/kpis` can take ~14 s and `/api/data-quality` ~8 s on a
cold cache. Results are cached for 15 minutes, so this is a one-time cost per
preset and not a sign of a problem.

## Tabs

| Tab | What it covers |
| --- | --- |
| Executive Overview | Company-wide revenue, gross profit and margin KPIs with trend, department mix, a data-quality panel explaining exclusions such as quote-only lines, and an invoice register searchable by invoice number, document number, customer name or customer number |
| Units / Sales | Equipment sales performance by category, condition, make and salesperson, plus unit inventory ageing |
| Parts | Parts revenue and margin by part group, manufacturer and stocking class, plus stock-on-hand buckets |
| Service | Work-order revenue and labour recovery by bill-as type, status and technician, plus WIP ageing |
| Rental | Rental revenue and utilisation by rental group and contract status, plus fleet ageing |
| Customers | Customer segmentation, revenue bands, geography, and top-customer drill-downs |

Each tab shares a global date-range control and supports sorting, filtering and
paging on its detail tables. The JSON API behind them is browsable at
<http://127.0.0.1:8000/docs>.

## Health check

```bash
curl http://127.0.0.1:8000/api/health
```

Returns the resolved database filename, its size, the table count and cache
statistics — the quickest way to confirm the database was found.
