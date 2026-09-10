/* Perseus BI Dashboard - application shell.
 *
 * Owns everything shared across tabs: the date range, the trend grain, the
 * data-quality banner and hash routing. The tabs themselves live in the
 * tab-*.jsx files loaded ahead of this one; this file must stay last in
 * index.html because it is the only one that references them.
 */

const TABS = [
  { key: "overview", label: "Executive Overview", title: "Executive Overview" },
  { key: "units", label: "Units / Sales", title: "Units & Sales" },
  { key: "parts", label: "Parts", title: "Parts" },
  { key: "service", label: "Service", title: "Service" },
  { key: "rental", label: "Rental", title: "Rental" },
  { key: "customers", label: "Customers", title: "Customers" },
];

const TAB_KEYS = TABS.map((t) => t.key);

/* ------------------------------------------------------- quality banner */

function DataQuality({ params }) {
  const [open, setOpen] = useState(
    () => new URLSearchParams(location.search).get("notes") === "open"
  );
  const { data } = useEndpoint("/api/data-quality", params);
  const issues = (data && data.issues) || [];
  if (issues.length === 0) return null;

  const high = issues.filter((i) => i.impact === "high").length;

  return (
    <div className="dq">
      <div className="dq-head" onClick={() => setOpen(!open)}>
        <span className="chev">{open ? "\u25bc" : "\u25b6"}</span>
        <strong>How to read these numbers</strong>
        <span className="count">
          {issues.length} note{issues.length === 1 ? "" : "s"}
          {high > 0 ? `, ${high} high impact` : ""} - click to {open ? "hide" : "expand"}
        </span>
      </div>
      {open && (
        <div className="dq-body">
          {issues.map((it, i) => (
            <div className="dq-item" key={it.code || i}>
              <span className="title">{it.title}</span>
              <span className={`impact impact-${it.impact || "low"}`}>{it.impact}</span>
              <div className="detail">{it.detail}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- routing */

function tabFromHash() {
  const key = (location.hash || "").replace(/^#\/?/, "").split("?")[0];
  return TAB_KEYS.includes(key) ? key : "overview";
}

/** Tab lives in the hash, period and grain in the query string, so any view
 * can be linked and survives a reload. */
function useHashTab() {
  const [tab, setTab] = useState(tabFromHash);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = useCallback((key) => {
    if (tabFromHash() === key) setTab(key);
    else location.hash = `#/${key}`;
  }, []);

  return [tab, go];
}

function TabBar({ tab, onTab }) {
  return (
    <nav className="tabs">
      {TABS.map((t) => (
        <a
          key={t.key}
          href={`#/${t.key}`}
          className={t.key === tab ? "on" : ""}
          onClick={(e) => {
            e.preventDefault();
            onTab(t.key);
          }}
        >
          {t.label}
        </a>
      ))}
    </nav>
  );
}

/* ------------------------------------------------------------------- app */

/** Initial preset and grain may be set from the URL, so a view can be shared. */
function initialView() {
  const p = new URLSearchParams(location.search);
  const preset = p.get("preset");
  const grain = p.get("grain");
  return {
    preset: ["ytd", "12m", "3y", "all"].includes(preset) ? preset : "12m",
    grain: ["month", "quarter", "year"].includes(grain) ? grain : "month",
  };
}

function App() {
  const meta = useEndpoint("/api/meta", {});
  const view = useMemo(initialView, []);
  const [range, setRange] = useState(null);
  const [preset, setPreset] = useState(view.preset);
  const [grain, setGrain] = useState(view.grain);
  const [tab, goTab] = useHashTab();
  // Drill-downs that cross tabs hand their filter over here; the receiving tab
  // copies it into its own table state, so clearing a chip does not fight the
  // hand-off.
  const [handoff, setHandoff] = useState(null);

  const bounds = meta.data;

  const applyPreset = useCallback(
    (key, b) => {
      const src = b || bounds;
      if (!src) return;
      const max = src.max_date;
      const min = src.min_date;
      setPreset(key);
      if (key === "all") setRange({ start: min, end: max });
      else if (key === "ytd") setRange({ start: max.slice(0, 4) + "-01-01", end: max });
      else if (key === "12m") setRange({ start: addYears(max, -1), end: max });
      else if (key === "3y") setRange({ start: addYears(max, -3), end: max });
    },
    [bounds]
  );

  useEffect(() => {
    if (bounds && !range) applyPreset(view.preset, bounds);
  }, [bounds]);

  const onGoTab = useCallback(
    (key, filters) => {
      setHandoff({ tab: key, filters: filters || {} });
      goTab(key);
    },
    [goTab]
  );

  const params = useMemo(
    () => (range ? { start: range.start, end: range.end } : null),
    [range]
  );

  if (meta.loading) return <div className="boot">Loading dashboard...</div>;
  if (meta.error)
    return (
      <div className="err" style={{ padding: 40 }}>
        Could not reach the API: {meta.error}
      </div>
    );

  const loc = bounds.location || {};
  const current = TABS.find((t) => t.key === tab) || TABS[0];
  const initialFilters = handoff && handoff.tab === tab ? handoff.filters : null;

  const tabProps = { params, grain, onGrain: setGrain, initialFilters };

  return (
    <div className="app">
      <div className="header">
        <div>
          <h1>
            {loc.CompanyName || "Perseus"} - {current.title}
          </h1>
          <div className="sub">
            {loc.City}, {loc.State} &nbsp;·&nbsp; {bounds.min_date} to {bounds.max_date}{" "}
            &nbsp;·&nbsp;{" "}
            <span title="Booked invoices carrying revenue lines, out of all booked invoices">
              {num(bounds.revenue_invoices)} of {num(bounds.booked_invoices)} booked invoices
              carry revenue lines
            </span>
          </div>
        </div>
        <div className="controls">
          <div className="control-group">
            <label>Period</label>
            <Segmented
              value={preset}
              options={[
                { key: "ytd", label: "YTD" },
                { key: "12m", label: "12M" },
                { key: "3y", label: "3Y" },
                { key: "all", label: "All" },
              ]}
              onChange={applyPreset}
            />
          </div>
          <div className="control-group">
            <label>Custom range</label>
            <div className="dates">
              <input
                type="date"
                value={(range && range.start) || ""}
                min={bounds.min_date}
                max={bounds.max_date}
                onChange={(e) => {
                  setPreset("custom");
                  setRange((r) => ({ ...r, start: e.target.value }));
                }}
              />
              <span>to</span>
              <input
                type="date"
                value={(range && range.end) || ""}
                min={bounds.min_date}
                max={bounds.max_date}
                onChange={(e) => {
                  setPreset("custom");
                  setRange((r) => ({ ...r, end: e.target.value }));
                }}
              />
            </div>
          </div>
        </div>
      </div>

      <TabBar tab={tab} onTab={goTab} />

      {params && <DataQuality params={params} />}

      {params && tab === "overview" && (
        <OverviewTab params={params} grain={grain} onGrain={setGrain} onGoTab={onGoTab} />
      )}
      {params && tab === "units" && <UnitsTab {...tabProps} />}
      {params && tab === "parts" && <PartsTab {...tabProps} />}
      {params && tab === "service" && <ServiceTab {...tabProps} />}
      {params && tab === "rental" && <RentalTab {...tabProps} />}
      {params && tab === "customers" && <CustomersTab params={params} initialFilters={initialFilters} />}

      <div className="footer">
        Perseus BI · read-only over <code>perseus_equipment_database.db</code>
        <br />
        Revenue counts finalized and archived invoices only. Gross margin covers Units and Parts,
        the only lines with a cost basis. Trade-ins are tracked separately, not as negative revenue.
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
