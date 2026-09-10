/* Parts tab.
 *
 * Parts is the other department with a genuine cost basis (SalePart.AvgCost),
 * so margin is real here. Two schema gaps shape this tab and are labelled where
 * they bite: there is no price-class table, and SalePart.ShelfLocation is not
 * a shelf location.
 */

function PartsKpis({ params }) {
  const { loading, data, error } = useEndpoint("/api/parts/summary", params);
  const d = data || {};

  const cards = [
    { label: "Parts Revenue", value: compactMoney(d.revenue) },
    { label: "Gross Profit", value: compactMoney(d.gross_profit), note: "AvgCost basis" },
    { label: "Gross Margin", value: pct(d.margin_pct) },
    { label: "Lines", value: compactNum(d.lines), note: `${num(d.parts_sold)} distinct parts` },
    { label: "Avg Line Value", value: money(d.avg_line_value, 2) },
    {
      label: "Fill Rate",
      value: pct(d.fill_rate_pct),
      note: `qty vs requested, ${num(d.short_lines)} short`,
    },
    {
      label: "Below Cost",
      value: num(d.below_cost_lines),
      note: "lines sold under AvgCost",
      tone: d.below_cost_lines > 0 ? "warn" : null,
    },
    {
      label: "Never Sold",
      value: num(d.never_sold),
      note: `of ${num(d.parts_on_file)} active parts on file`,
    },
  ];

  return <KpiGrid loading={loading} error={error} cards={cards} />;
}

function PartsGroupPanel({ data, loading, error, onDrill }) {
  const [view, setView] = useState("groups");
  const rows = (data && data[view]) || [];
  const labelKey = view === "groups" ? "part_group" : "manufacturer";
  const filterKey = view === "groups" ? "part_group" : "manufacturer";

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r[labelKey]);
    return {
      animationDuration: 350,
      grid: { top: 30, left: 10, right: 56, bottom: 26, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x[labelKey] === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Revenue <b>${money(r.revenue)}</b><br/>Gross profit <b>${money(r.gross_profit)}</b><br/>
            Margin <b>${pct(r.margin_pct)}</b><br/>
            <span style="color:#8b94a7">${num(r.lines)} lines &middot; ${num(r.qty)} qty</span>`;
        },
      },
      xAxis: catAxis(labels, labels.length > 6 ? 30 : 0),
      yAxis: [
        valAxis("Revenue", (v) => compactMoney(v)),
        { ...valAxis("GM %", "{value}%"), splitLine: { show: false } },
      ],
      series: [
        {
          name: "Gross profit",
          type: "bar",
          stack: "r",
          barMaxWidth: 44,
          itemStyle: { color: PALETTE[2] },
          data: rows.map((r) => +(r.gross_profit || 0).toFixed(2)),
        },
        {
          name: "Cost",
          type: "bar",
          stack: "r",
          barMaxWidth: 44,
          itemStyle: { color: "#33405a" },
          data: rows.map((r) => +(r.cost || 0).toFixed(2)),
        },
        {
          name: "Margin %",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { width: 2, color: "#f7b32b" },
          itemStyle: { color: "#f7b32b" },
          data: rows.map((r) => (r.margin_pct == null ? null : +r.margin_pct.toFixed(2))),
        },
      ],
    };
  }, [data, view]);

  return (
    <Panel
      title="Margin by Part Group"
      right={
        <Segmented
          value={view}
          options={[
            { key: "groups", label: "Group" },
            { key: "manufacturers", label: "Manufacturer" },
          ]}
          onChange={setView}
        />
      }
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={266}
            onClick={(p) => onDrill({ [filterKey]: p.name })}
          />
          {view === "groups" && (
            <Caveat>
              Only 6 part groups exist and PartMaster.PartGroupId is unset on most parts, so
              roughly 91% of parts revenue lands in Unclassified. The manufacturer view is
              better populated and is the more useful cut of the same revenue.
            </Caveat>
          )}
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function PartsBandPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.bands) || [];
  const usable = rows.filter((r) => r.lines > 0);

  const option = useMemo(() => {
    if (!usable.length) return null;
    const labels = usable.map((r) => r.band);
    return {
      animationDuration: 350,
      grid: { top: 30, left: 12, right: 56, bottom: 40, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = usable.find((x) => x.band === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Revenue <b>${money(r.revenue)}</b><br/>Gross profit <b>${money(r.gross_profit)}</b><br/>
            <span style="color:#8b94a7">${num(r.lines)} lines</span>`;
        },
      },
      xAxis: catAxis(labels, 30),
      yAxis: [
        valAxis("Revenue", (v) => compactMoney(v)),
        { ...valAxis("Lines", (v) => compactNum(v)), splitLine: { show: false } },
      ],
      series: [
        {
          name: "Revenue",
          type: "bar",
          barMaxWidth: 40,
          data: usable.map((r, i) => ({
            value: +(r.revenue || 0).toFixed(2),
            itemStyle: {
              color:
                r.band === "below cost" || r.band === "credit / zero"
                  ? "#e8615d"
                  : r.band === "no cost basis"
                  ? "#6b7488"
                  : RAMP[Math.max(0, RAMP.length - 1 - i)],
              borderRadius: [3, 3, 0, 0],
            },
          })),
        },
        {
          name: "Lines",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: "#e6e9ef" },
          itemStyle: { color: "#e6e9ef" },
          data: usable.map((r) => r.lines),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Realised Margin Bands" hint="stands in for price class">
      <Body loading={loading} error={error} empty={!usable.length}>
        <React.Fragment>
          <EChart option={option} height={266} onClick={(p) => onDrill({ band: p.name })} />
          <Caveat>
            A true price-class cut is not available: PartMaster.PriceClassId is 4 on all
            16,208 parts, PartLocation.PriceClassId is empty, and the schema has no
            price-class table. These are realised per-line margin bands instead, which
            answer the same pricing question from the outcome rather than the setup.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function PartsMoversPanel({ data, loading, error, onDrill }) {
  const [metric, setMetric] = useState("revenue");
  const rows = (data && data.movers) || [];
  const ranked = useMemo(
    () => rows.slice().sort((a, b) => (b[metric] || 0) - (a[metric] || 0)).slice(0, 12),
    [data, metric]
  );

  const option = useMemo(() => {
    if (!ranked.length) return null;
    return rankedBarOption({
      rows: ranked,
      labelKey: "part_no",
      valueKey: metric,
      format: metric === "revenue" ? compactMoney : (v) => num(v),
      color: PALETTE[0],
      tooltip: (r) =>
        `<span style="color:#8b94a7">${text(r.description)}</span><br/>Revenue <b>${money(
          r.revenue
        )}</b><br/>Margin <b>${pct(r.margin_pct)}</b><br/>Qty <b>${num(r.qty)}</b> over ${num(
          r.lines
        )} lines`,
    });
  }, [data, metric]);

  return (
    <Panel
      title="Top Movers"
      right={
        <Segmented
          value={metric}
          options={[
            { key: "revenue", label: "Revenue" },
            { key: "qty", label: "Quantity" },
          ]}
          onChange={setMetric}
        />
      }
    >
      <Body loading={loading} error={error} empty={!ranked.length}>
        <EChart option={option} height={300} onClick={(p) => onDrill({ search: p.name })} />
      </Body>
    </Panel>
  );
}

function PartsMovementPanel({ data, loading, error, asOf, onDrill }) {
  const rows = (data && data.movement) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return {
      animationDuration: 350,
      grid: { top: 30, left: 12, right: 16, bottom: 40, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.bucket === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Parts <b>${num(r.parts)}</b><br/>Of which min/max stocked <b>${num(
            r.stocked
          )}</b><br/>Lifetime revenue <b>${compactMoney(r.revenue)}</b>`;
        },
      },
      xAxis: catAxis(rows.map((r) => r.bucket), 30),
      yAxis: valAxis("Parts"),
      series: [
        {
          name: "All parts",
          type: "bar",
          barMaxWidth: 40,
          data: rows.map((r, i) => ({
            value: r.parts,
            itemStyle: { color: RAMP[Math.min(i, RAMP.length - 1)], borderRadius: [3, 3, 0, 0] },
          })),
        },
        {
          name: "Min/max stocked",
          type: "bar",
          barMaxWidth: 14,
          barGap: "-70%",
          itemStyle: { color: "rgba(230,233,239,.75)" },
          data: rows.map((r) => r.stocked),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Dead & Slow Stock" hint={`by last sale, as of ${asOf || "-"}`}>
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={252} onClick={(p) => onDrill({ bucket: p.name })} />
          <Caveat>
            The overlaid bars are parts the branch deliberately stocks
            (PartLocation.OFCCode = minmax); the rest are ordered on demand, so a long gap
            since the last sale is expected for them. There is no on-hand quantity column in
            this schema, so this measures movement, not tied-up capital.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function PartsLocationPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.locations) || [];
  const stocking = (data && data.stocking_classes) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 12),
      labelKey: "bin",
      valueKey: "revenue",
      color: PALETTE[3],
      tooltip: (r) =>
        `Revenue <b>${money(r.revenue)}</b><br/>Margin <b>${pct(
          r.margin_pct
        )}</b><br/><span style="color:#8b94a7">${num(r.parts)} parts &middot; ${num(
          r.lines
        )} lines &middot; ${num(r.qty)} qty</span>`,
    });
  }, [data]);

  return (
    <Panel title="Shelf Locations" hint="revenue by bin">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={244} onClick={(p) => onDrill({ bin: p.name })} />
          <table>
            <thead>
              <tr>
                <th>Stocking Class</th>
                <th className="num">Lines</th>
                <th className="num">Revenue</th>
                <th className="num">GM %</th>
              </tr>
            </thead>
            <tbody>
              {stocking.map((r) => (
                <tr
                  key={r.stocking_class}
                  className="rowlink"
                  onClick={() => onDrill({ stocking_class: r.stocking_class })}
                >
                  <td>{r.stocking_class}</td>
                  <td className="num">{compactNum(r.lines)}</td>
                  <td className="num">{compactMoney(r.revenue)}</td>
                  <td className="num">{pct(r.margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Caveat>
            Bins come from PartLocation.Bin, which is set on 1,634 of 16,066 parts.
            SalePart.ShelfLocation is deliberately not used: it is blank on 236,269 of
            248,162 lines and where populated it holds customer names and phone numbers.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function PartsTimelinePanel({ data, loading, error, grain, onGrain }) {
  const rows = (data && data.timeline) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r.period);
    return {
      animationDuration: 350,
      grid: { top: 32, left: 56, right: 56, bottom: labels.length > 18 ? 44 : 30 },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.period === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Revenue <b>${compactMoney(r.revenue)}</b><br/>Gross profit <b>${compactMoney(
            r.gross_profit
          )}</b><br/>Margin <b>${pct(r.margin_pct)}</b><br/>
            <span style="color:#8b94a7">${num(r.lines)} lines &middot; ${num(r.qty)} qty</span>`;
        },
      },
      xAxis: catAxis(labels),
      yAxis: [
        valAxis("Revenue", (v) => compactMoney(v)),
        { ...valAxis("GM %", "{value}%"), splitLine: { show: false } },
      ],
      series: [
        {
          name: "Gross profit",
          type: "bar",
          stack: "r",
          barMaxWidth: 30,
          itemStyle: { color: PALETTE[2] },
          data: rows.map((r) => +(r.gross_profit || 0).toFixed(2)),
        },
        {
          name: "Cost",
          type: "bar",
          stack: "r",
          barMaxWidth: 30,
          itemStyle: { color: "#33405a" },
          data: rows.map((r) => +(r.cost || 0).toFixed(2)),
        },
        {
          name: "Margin %",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2, color: "#f7b32b" },
          data: rows.map((r) => (r.margin_pct == null ? null : +r.margin_pct.toFixed(2))),
        },
      ],
      dataZoom: labels.length > 24 ? [{ type: "inside" }] : undefined,
    };
  }, [data]);

  return (
    <Panel
      title="Parts Revenue & Margin Trend"
      right={<Segmented value={grain} options={GRAINS} onChange={onGrain} />}
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <EChart option={option} height={260} />
      </Body>
    </Panel>
  );
}

/* ------------------------------------------------------------------- tab */

const PART_LINE_COLUMNS = [
  col.date("activity_date", "Date"),
  col.text("doc_no", "Doc"),
  col.text("part_no", "Part No"),
  col.text("description", "Description", {
    render: (r) => <span className="name">{text(r.description)}</span>,
  }),
  col.text("part_group", "Group"),
  col.text("manufacturer", "Mfg"),
  col.text("bin", "Bin"),
  col.text("customer_name", "Customer", {
    render: (r) => <span className="name">{text(r.customer_name)}</span>,
  }),
  col.num("qty", "Qty", 1),
  col.exact("unit_price", "Price"),
  col.exact("avg_cost", "Avg Cost"),
  col.exact("revenue", "Net"),
  col.exact("gross_profit", "GP"),
  col.pct("margin_pct", "GM %"),
];

const PART_STOCK_COLUMNS = [
  col.text("part_no", "Part No"),
  col.text("description", "Description", {
    render: (r) => <span className="name">{text(r.description)}</span>,
  }),
  col.text("part_group", "Group"),
  col.text("manufacturer", "Mfg"),
  col.text("stocking_class", "Stocking", { hint: "PartLocation.OFCCode" }),
  col.text("bin", "Bin"),
  col.num("min_stock", "Min"),
  col.num("max_stock", "Max"),
  col.date("last_sold", "Last Sold"),
  col.num("days_since", "Days Since"),
  col.num("lines", "Lines"),
  col.num("qty", "Qty", 1),
  col.money("revenue", "Revenue"),
  col.pct("margin_pct", "GM %"),
];

function PartsTab({ params, grain, onGrain, initialFilters }) {
  const charts = useEndpoint("/api/parts/charts", params ? { ...params, grain } : null);
  const summary = useEndpoint("/api/parts/summary", params);
  const [lineFilters, setLineFilters] = useState(initialFilters || {});
  const [stockFilters, setStockFilters] = useState({});

  useEffect(() => {
    if (initialFilters) setLineFilters(initialFilters);
  }, [JSON.stringify(initialFilters || {})]);

  const asOf = summary.data && summary.data.as_of;
  const shared = { data: charts.data, loading: charts.loading, error: charts.error };

  return (
    <React.Fragment>
      <PartsKpis params={params} />

      <div className="grid g-2">
        <PartsGroupPanel {...shared} onDrill={setLineFilters} />
        <PartsBandPanel {...shared} onDrill={setLineFilters} />
      </div>

      <div className="grid g-3">
        <PartsMoversPanel {...shared} onDrill={setLineFilters} />
        <PartsMovementPanel {...shared} asOf={asOf} onDrill={setStockFilters} />
        <PartsLocationPanel {...shared} onDrill={setLineFilters} />
      </div>

      <div className="grid g-1">
        <PartsTimelinePanel {...shared} grain={grain} onGrain={onGrain} />
      </div>

      <Panel title="Parts Sale Lines" hint="every booked PA line in range">
        <DataTable
          path="/api/parts/detail"
          params={params}
          columns={PART_LINE_COLUMNS}
          filters={lineFilters}
          onFilters={setLineFilters}
          defaultSort="revenue"
          searchHint="Search part no, description, customer, doc"
        />
      </Panel>

      <Panel title="Parts Stock & Movement" hint={`active parts master, as of ${asOf || "-"}`}>
        <DataTable
          path="/api/parts/stock"
          params={params}
          columns={PART_STOCK_COLUMNS}
          filters={stockFilters}
          onFilters={setStockFilters}
          defaultSort="days_since"
          searchHint="Search part no or description"
          note="Revenue and margin here are lifetime to the end of the window, not the window itself, so a part's whole history is visible next to how long it has sat."
        />
      </Panel>
    </React.Fragment>
  );
}
