/* Perseus BI Dashboard - shared primitives
 *
 * No build step: React and ECharts are loaded as globals from /static/vendor
 * and every .jsx file is transpiled in the browser by Babel standalone.
 *
 * The files listed in index.html run in order and share one top-level scope, so
 * anything defined here is visible to every tab file that follows it. Nothing
 * in this file may depend on a tab file.
 */

const { useState, useEffect, useRef, useMemo, useCallback } = React;

/* ------------------------------------------------------------------ config */

const DEPTS = [
  { key: "Sales", color: "#f7b32b" },
  { key: "Parts", color: "#4c9be8" },
  { key: "Service", color: "#4ec9a5" },
  { key: "Rental", color: "#b07ce8" },
  { key: "Other", color: "#6b7488" },
];

const DEPT_COLOR = DEPTS.reduce((acc, d) => ({ ...acc, [d.key]: d.color }), {});

/* Categorical series colours for the per-department tabs. Ordered so adjacent
 * slices of a donut stay distinguishable. */
const PALETTE = [
  "#4c9be8",
  "#f7b32b",
  "#4ec9a5",
  "#b07ce8",
  "#e8615d",
  "#5ec8d8",
  "#e8a33a",
  "#c8d84e",
  "#d85ea0",
  "#7c9be8",
  "#4ee8c9",
  "#6b7488",
];

/* Ordered buckets read worst-to-best or oldest-to-newest, so they get a ramp
 * rather than arbitrary hues. */
const RAMP = ["#4ec9a5", "#8ad07a", "#c8d84e", "#f7b32b", "#e8863a", "#e8615d"];

const GRAINS = [
  { key: "month", label: "Month" },
  { key: "quarter", label: "Quarter" },
  { key: "year", label: "Year" },
];

const AXIS_LINE = "#262d3d";
const TOOLTIP_STYLE = {
  backgroundColor: "#1c2230",
  borderColor: "#333c50",
  textStyle: { color: "#e6e9ef", fontSize: 12 },
  extraCssText: "box-shadow:0 4px 16px rgba(0,0,0,.5);border-radius:8px;",
};

/* ----------------------------------------------------------------- helpers */

function money(n, digits = 0) {
  if (n === null || n === undefined || isNaN(n)) return "-";
  const neg = n < 0;
  const v = Math.abs(n);
  const s = v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return (neg ? "-$" : "$") + s;
}

function compactMoney(n) {
  if (n === null || n === undefined || isNaN(n)) return "-";
  const neg = n < 0;
  const v = Math.abs(n);
  let s;
  if (v >= 1e9) s = (v / 1e9).toFixed(2) + "B";
  else if (v >= 1e6) s = (v / 1e6).toFixed(v >= 1e7 ? 1 : 2) + "M";
  else if (v >= 1e3) s = Math.round(v / 1e3) + "K";
  else s = v.toFixed(0);
  return (neg ? "-$" : "$") + s;
}

function num(n, digits = 0) {
  if (n === null || n === undefined || isNaN(n)) return "-";
  return n.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function compactNum(n) {
  if (n === null || n === undefined || isNaN(n)) return "-";
  const v = Math.abs(n);
  if (v >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (v >= 1e4) return Math.round(n / 1e3) + "K";
  return num(n);
}

function pct(n, digits = 1) {
  if (n === null || n === undefined || isNaN(n)) return "-";
  return n.toFixed(digits) + "%";
}

function hours(n) {
  if (n === null || n === undefined || isNaN(n)) return "-";
  return num(n, n < 100 ? 1 : 0) + "h";
}

function days(n) {
  if (n === null || n === undefined || isNaN(n)) return "-";
  return num(n, 0) + "d";
}

function text(v, fallback = "-") {
  if (v === null || v === undefined) return fallback;
  const s = String(v).trim();
  return s === "" ? fallback : s;
}

function deltaClass(d) {
  if (d === null || d === undefined || isNaN(d)) return "flat";
  if (d > 0.05) return "up";
  if (d < -0.05) return "down";
  return "flat";
}

function deltaText(d) {
  if (d === null || d === undefined || isNaN(d)) return "n/a";
  const arrow = d > 0.05 ? "\u25b2" : d < -0.05 ? "\u25bc" : "\u25ac";
  return `${arrow} ${Math.abs(d).toFixed(1)}%`;
}

function qs(params) {
  const p = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") p.set(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function addYears(iso, n) {
  const d = new Date(iso + "T00:00:00");
  d.setFullYear(d.getFullYear() + n);
  return d.toISOString().slice(0, 10);
}

/* -------------------------------------------------------------- data layer */

/** Fetch a JSON endpoint, keyed on its querystring. */
function useEndpoint(path, params) {
  const key = path + qs(params);
  const enabled = !!params;
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));

    fetch(key)
      .then(async (r) => {
        if (!r.ok) {
          let msg = `${r.status} ${r.statusText}`;
          try {
            const body = await r.json();
            if (body && body.detail) msg = body.detail;
          } catch (e) {
            /* response was not JSON */
          }
          throw new Error(msg);
        }
        return r.json();
      })
      .then((data) => {
        if (alive) setState({ loading: false, data, error: null });
      })
      .catch((err) => {
        if (alive) setState({ loading: false, data: null, error: err.message });
      });

    return () => {
      alive = false;
    };
  }, [key, enabled]);

  return state;
}

/* --------------------------------------------------------------- charting */

function EChart({ option, height = 300, className = "", onClick }) {
  const ref = useRef(null);
  const chart = useRef(null);
  const handler = useRef(onClick);
  handler.current = onClick;

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, null, { renderer: "canvas" });
    // Bound once through a ref so a chart is never torn down and rebuilt just
    // because a parent re-rendered with a fresh arrow function.
    chart.current.on("click", (params) => {
      if (handler.current) handler.current(params);
    });
    const ro = new ResizeObserver(() => chart.current && chart.current.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current && chart.current.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    if (chart.current && option) {
      chart.current.setOption(option, { notMerge: true });
    }
  }, [option]);

  return (
    <div
      ref={ref}
      className={`chart ${onClick ? "chart-clickable" : ""} ${className}`}
      style={{ height }}
    />
  );
}

/** Shared axis styling, so every tab's charts line up with the Overview. */
const catAxis = (labels, rotate) => ({
  type: "category",
  data: labels,
  axisLabel: {
    color: "#8b94a7",
    fontSize: 10.5,
    rotate: rotate === undefined ? (labels.length > 14 ? 45 : 0) : rotate,
    interval: 0,
  },
  axisLine: { lineStyle: { color: AXIS_LINE } },
  axisTick: { show: false },
});

const valAxis = (name, formatter) => ({
  type: "value",
  name: name || undefined,
  nameTextStyle: { color: "#8b94a7", fontSize: 10.5 },
  axisLabel: { color: "#8b94a7", fontSize: 10.5, formatter: formatter || undefined },
  splitLine: { lineStyle: { color: "#1e2431" } },
});

const legendStyle = {
  top: 0,
  left: "center",
  textStyle: { color: "#8b94a7", fontSize: 11 },
  itemWidth: 10,
  itemHeight: 10,
  itemGap: 12,
  icon: "roundRect",
};

/** Horizontal ranked bars, the workhorse chart of the detail tabs. */
function rankedBarOption({ rows, labelKey, valueKey, format, color, tooltip }) {
  const ordered = rows.slice().reverse();
  const byLabel = ordered.reduce((acc, r) => ({ ...acc, [r[labelKey]]: r }), {});
  const fmt = format || compactMoney;
  return {
    animationDuration: 350,
    grid: { top: 6, left: 8, right: 66, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      ...TOOLTIP_STYLE,
      formatter: (ps) => {
        if (!ps.length) return "";
        const r = byLabel[ps[0].name] || {};
        return `<div style="font-weight:650;margin-bottom:4px">${ps[0].name}</div>${
          tooltip ? tooltip(r) : `<b>${fmt(r[valueKey])}</b>`
        }`;
      },
    },
    xAxis: valAxis(null, (v) => fmt(v)),
    yAxis: {
      type: "category",
      data: ordered.map((r) => r[labelKey]),
      axisLabel: { color: "#8b94a7", fontSize: 11 },
      axisLine: { lineStyle: { color: AXIS_LINE } },
      axisTick: { show: false },
    },
    series: [
      {
        type: "bar",
        data: ordered.map((r) => ({
          value: +(r[valueKey] || 0).toFixed(2),
          itemStyle: {
            color:
              typeof color === "function" ? color(r) : color || PALETTE[0],
            borderRadius: [0, 3, 3, 0],
          },
        })),
        barMaxWidth: 16,
        label: {
          show: true,
          position: "right",
          color: "#8b94a7",
          fontSize: 10.5,
          formatter: (p) => fmt(p.value),
        },
      },
    ],
  };
}

/** Single donut with a centre caption. */
function donutOption({ rows, labelKey, valueKey, caption, format, colors }) {
  const fmt = format || compactMoney;
  const usable = rows.filter((r) => (r[valueKey] || 0) > 0);
  return {
    animationDuration: 350,
    tooltip: {
      trigger: "item",
      ...TOOLTIP_STYLE,
      formatter: (p) => `${p.marker}${p.name}<br/><b>${fmt(p.value)}</b> (${p.percent}%)`,
    },
    title: caption
      ? [
          {
            text: caption,
            left: "center",
            top: "46%",
            textAlign: "center",
            textStyle: { color: "#8b94a7", fontSize: 11, fontWeight: 500 },
          },
        ]
      : undefined,
    series: [
      {
        type: "pie",
        radius: ["44%", "66%"],
        center: ["50%", "52%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: "#161b26", borderWidth: 2 },
        label: {
          color: "#8b94a7",
          fontSize: 10.5,
          formatter: (p) => `${p.name}\n${p.percent.toFixed(0)}%`,
        },
        labelLine: { length: 6, length2: 8, lineStyle: { color: "#333c50" } },
        data: usable.map((r, i) => ({
          name: r[labelKey],
          value: +(r[valueKey] || 0).toFixed(2),
          itemStyle: { color: (colors || PALETTE)[i % (colors || PALETTE).length] },
        })),
      },
    ],
  };
}

/* ------------------------------------------------------------- primitives */

function Panel({ title, hint, children, right, sketch, wide }) {
  return (
    <div className={`panel ${wide ? "panel-wide" : ""}`}>
      <div className="panel-head">
        <h2>
          {title}
          {sketch && <span className="sketch-tag">sketch</span>}
        </h2>
        {right || (hint && <span className="hint">{hint}</span>)}
      </div>
      <div className="panel-body">{children}</div>
    </div>
  );
}

function Loading() {
  return <div className="loading">Loading...</div>;
}

function Err({ msg }) {
  return <div className="err">Could not load: {msg}</div>;
}

function Empty({ msg }) {
  return <div className="empty">{msg || "Nothing in this range"}</div>;
}

/** Renders whichever of loading / error / empty / content applies. */
function Body({ loading, error, empty, emptyMsg, children }) {
  if (loading) return <Loading />;
  if (error) return <Err msg={error} />;
  if (empty) return <Empty msg={emptyMsg} />;
  return children;
}

function Segmented({ value, options, onChange }) {
  return (
    <div className="segmented">
      {options.map((o) => (
        <button
          key={o.key}
          className={value === o.key ? "on" : ""}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A caveat attached to a number, for anything that is a proxy or incomplete. */
function Caveat({ children }) {
  return <div className="caveat">{children}</div>;
}

/* ------------------------------------------------------------- KPI section */

function KpiCard({ label, value, deltas, spark, note, tone }) {
  const sparkOption = useMemo(() => {
    if (!spark || spark.length < 2) return null;
    return {
      animation: false,
      grid: { top: 2, bottom: 2, left: 2, right: 2 },
      xAxis: { type: "category", show: false, data: spark.map((_, i) => i) },
      yAxis: { type: "value", show: false, min: "dataMin", max: "dataMax" },
      tooltip: { show: false },
      series: [
        {
          type: "line",
          data: spark,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.6, color: "#4c9be8" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(76,155,232,.35)" },
              { offset: 1, color: "rgba(76,155,232,0)" },
            ]),
          },
        },
      ],
    };
  }, [spark]);

  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className={`value ${tone || ""}`}>{value}</div>
      {deltas && deltas.length > 0 && (
        <div className="deltas">
          {deltas.map((d) => (
            <span key={d.key} className="delta">
              <span className="dk">{d.key}</span>
              <span className={deltaClass(d.value)}>{deltaText(d.value)}</span>
            </span>
          ))}
        </div>
      )}
      {note && <div className="note">{note}</div>}
      {sparkOption && <EChart option={sparkOption} height={30} className="spark" />}
    </div>
  );
}

/** KPI strip for the detail tabs.
 *
 * These tabs deliberately carry no period-over-period deltas: computing them
 * would double every aggregation, and an unlabelled blank is better than an
 * invented comparison.
 */
function KpiGrid({ loading, error, cards }) {
  if (loading)
    return (
      <div className="kpis">
        <div className="kpi">
          <Loading />
        </div>
      </div>
    );
  if (error) return <Err msg={error} />;
  return (
    <div className="kpis">
      {cards.map((c) => (
        <KpiCard
          key={c.label}
          label={c.label}
          value={c.value}
          note={c.note}
          tone={c.tone}
        />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------- data table */

/**
 * Sortable, paged detail table backed by one of the `/detail`-style endpoints.
 *
 * Sorting and paging are done by the server: several of these tables cover
 * hundreds of thousands of rows, so the browser only ever holds one page.
 * `columns` entries are `{ key, label, align, render, hint }` and `key` must be
 * a column the endpoint accepts as a sort key.
 *
 * `filters` is the drill-down state owned by the tab. Each entry is shown as a
 * removable chip so it is always obvious why the table is showing a subset.
 */
function DataTable({
  path,
  params,
  columns,
  filters,
  onFilters,
  defaultSort,
  defaultDir = "desc",
  searchable = true,
  searchHint = "Search",
  note,
}) {
  const [sort, setSort] = useState(defaultSort);
  const [dir, setDir] = useState(defaultDir);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setSearch(typed.trim()), 250);
    return () => clearTimeout(t);
  }, [typed]);

  const active = Object.entries(filters || {}).filter(([, v]) => v);
  const scope = JSON.stringify(active) + JSON.stringify(params || {}) + search + pageSize;
  useEffect(() => setPage(1), [scope]);

  const query = params
    ? {
        ...params,
        ...Object.fromEntries(active),
        page,
        page_size: pageSize,
        sort,
        dir,
        // Typed search wins over a drill-down that arrived as a search filter,
        // so the box always describes what the table is showing.
        ...(search ? { search } : {}),
      }
    : null;

  const { loading, data, error } = useEndpoint(path, query);
  const rows = (data && data.rows) || [];
  const total = (data && data.total) || 0;
  const pages = (data && data.pages) || 1;
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const onHeader = (key) => {
    if (key === sort) {
      setDir(dir === "desc" ? "asc" : "desc");
    } else {
      setSort(key);
      setDir("desc");
    }
    setPage(1);
  };

  return (
    <div className="dt">
      <div className="dt-bar">
        <div className="chips">
          {active.length === 0 ? (
            <span className="chips-empty">
              No filter - click a chart above to narrow this table
            </span>
          ) : (
            active.map(([k, v]) => (
              <button
                key={k}
                className="chip"
                title="Remove this filter"
                onClick={() => onFilters && onFilters({ ...filters, [k]: null })}
              >
                {k.replace(/_/g, " ")}: <b>{String(v)}</b> <span className="x">&times;</span>
              </button>
            ))
          )}
          {active.length > 1 && (
            <button className="chip chip-clear" onClick={() => onFilters && onFilters({})}>
              clear all
            </button>
          )}
        </div>
        {searchable && (
          <input
            className="dt-search"
            type="search"
            placeholder={searchHint}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
        )}
      </div>

      {note && <Caveat>{note}</Caveat>}

      {error ? (
        <Err msg={error} />
      ) : (
        <div className="dt-scroll">
          <table className="dt-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    className={`sortable ${c.align === "num" ? "num" : ""} ${
                      sort === c.key ? "sorted" : ""
                    }`}
                    title={c.hint || `Sort by ${c.label}`}
                    onClick={() => onHeader(c.key)}
                  >
                    {c.label}
                    <span className="arrow">
                      {sort === c.key ? (dir === "desc" ? "\u25be" : "\u25b4") : ""}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length}>
                    <Loading />
                  </td>
                </tr>
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length}>
                    <Empty msg="No rows match this filter" />
                  </td>
                </tr>
              ) : (
                rows.map((r, i) => (
                  <tr key={i} className={loading ? "stale" : ""}>
                    {columns.map((c) => (
                      <td key={c.key} className={c.align === "num" ? "num" : ""}>
                        {c.render ? c.render(r) : text(r[c.key])}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="dt-foot">
        <span className="dt-count">
          {total === 0 ? "no rows" : `${num(from)}-${num(to)} of ${num(total)}`}
        </span>
        <div className="dt-pager">
          <button disabled={page <= 1} onClick={() => setPage(1)}>
            &laquo;
          </button>
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
            &lsaquo;
          </button>
          <span className="dt-page">
            {num(page)} / {num(pages)}
          </span>
          <button disabled={page >= pages} onClick={() => setPage(page + 1)}>
            &rsaquo;
          </button>
          <button disabled={page >= pages} onClick={() => setPage(pages)}>
            &raquo;
          </button>
        </div>
        <Segmented
          value={pageSize}
          options={[
            { key: 25, label: "25" },
            { key: 50, label: "50" },
            { key: 100, label: "100" },
          ]}
          onChange={setPageSize}
        />
      </div>
    </div>
  );
}

/* Column builders, so the ten detail tables stay consistent. */
const col = {
  text: (key, label, extra) => ({ key, label, ...extra }),
  money: (key, label, extra) => ({
    key,
    label,
    align: "num",
    render: (r) => compactMoney(r[key]),
    ...extra,
  }),
  exact: (key, label, extra) => ({
    key,
    label,
    align: "num",
    render: (r) => money(r[key], 2),
    ...extra,
  }),
  num: (key, label, digits = 0, extra) => ({
    key,
    label,
    align: "num",
    render: (r) => num(r[key], digits),
    ...extra,
  }),
  pct: (key, label, extra) => ({
    key,
    label,
    align: "num",
    render: (r) => pct(r[key]),
    ...extra,
  }),
  hours: (key, label, extra) => ({
    key,
    label,
    align: "num",
    render: (r) => hours(r[key]),
    ...extra,
  }),
  date: (key, label, extra) => ({
    key,
    label,
    render: (r) => text(r[key] && String(r[key]).slice(0, 10), "never"),
    ...extra,
  }),
};

/** Tab scaffold: KPI strip, chart grid, then the detail table(s). */
function TabLayout({ kpis, children }) {
  return (
    <React.Fragment>
      {kpis}
      {children}
    </React.Fragment>
  );
}
