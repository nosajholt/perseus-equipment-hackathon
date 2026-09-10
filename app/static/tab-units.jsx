/* Units / Sales tab.
 *
 * Unit sales carry a real cost basis (SaleUnit.InvoiceCost), so this is one of
 * the two tabs where a gross margin is honest. Inventory on hand is a snapshot
 * aged against the end of the selected window, not a windowed total.
 */

function UnitsKpis({ params }) {
  const { loading, data, error } = useEndpoint("/api/units/summary", params);
  const d = data || {};

  const cards = [
    { label: "Unit Revenue", value: compactMoney(d.revenue) },
    { label: "Gross Profit", value: compactMoney(d.gross_profit), note: "InvoiceCost basis" },
    { label: "Gross Margin", value: pct(d.margin_pct) },
    {
      label: "Units Sold",
      value: num(d.units_sold),
      note: d.return_lines ? `${num(d.return_lines)} return lines included` : null,
    },
    { label: "Avg Selling Price", value: money(d.avg_selling_price) },
    {
      label: "New Mix",
      value: pct(d.new_mix_pct),
      note: `${num(d.new_lines)} new / ${num(d.used_lines)} used`,
    },
    {
      label: "Trade-In Allowance",
      value: compactMoney(d.trade_in_allowance),
      note: `${num(d.trade_ins)} taken, excluded from revenue`,
    },
    {
      label: "On Hand",
      value: num(d.on_hand_units),
      note: `${compactMoney(d.on_hand_cost)} cost, avg ${num(d.on_hand_avg_age_days)}d old`,
    },
  ];

  return <KpiGrid loading={loading} error={error} cards={cards} />;
}

function UnitsCategoryPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.categories) || [];
  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows,
      labelKey: "category",
      valueKey: "revenue",
      color: (r) => (r.margin_pct != null && r.margin_pct < 15 ? "#e8863a" : PALETTE[0]),
      tooltip: (r) =>
        `Revenue <b>${money(r.revenue)}</b><br/>Gross profit <b>${money(
          r.gross_profit
        )}</b><br/>Margin <b>${pct(r.margin_pct)}</b><br/><span style="color:#8b94a7">${num(
          r.units
        )} units &middot; ASP ${money(r.avg_price)}</span>`,
    });
  }, [data]);

  return (
    <Panel title="Revenue & Margin by Category" hint="click a bar to filter the table">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={200}
            onClick={(p) => onDrill({ category: p.name })}
          />
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th className="num">Units</th>
                <th className="num">Revenue</th>
                <th className="num">GP</th>
                <th className="num">GM %</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.category} className="rowlink" onClick={() => onDrill({ category: r.category })}>
                  <td>{r.category}</td>
                  <td className="num">{num(r.units)}</td>
                  <td className="num">{compactMoney(r.revenue)}</td>
                  <td className="num">{compactMoney(r.gross_profit)}</td>
                  <td className="num">{pct(r.margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function UnitsConditionPanel({ data, loading, error, onDrill }) {
  const conditions = (data && data.conditions) || [];
  const grades = (data && data.grades) || [];

  const option = useMemo(() => {
    if (!conditions.length) return null;
    return donutOption({
      rows: conditions,
      labelKey: "condition_class",
      valueKey: "revenue",
      caption: "Revenue",
      colors: ["#4c9be8", "#f7b32b"],
    });
  }, [data]);

  return (
    <Panel title="New vs Used" hint="condition grade below">
      <Body loading={loading} error={error} empty={!conditions.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={182}
            onClick={(p) => onDrill({ condition: p.name })}
          />
          <table>
            <thead>
              <tr>
                <th>Grade</th>
                <th className="num">Units</th>
                <th className="num">Revenue</th>
                <th className="num">GM %</th>
              </tr>
            </thead>
            <tbody>
              {grades.map((r) => (
                <tr key={r.condition_grade}>
                  <td>{r.condition_grade}</td>
                  <td className="num">{num(r.units)}</td>
                  <td className="num">{compactMoney(r.revenue)}</td>
                  <td className="num">{pct(r.margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Caveat>
            Grade comes from UnitBase.UnitConditionId, which is unset on some sold units;
            those show as Unrecorded. New/Used is SaleUnit.IsNew and is always populated.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function UnitsMakesPanel({ data, loading, error, onDrill }) {
  const [view, setView] = useState("makes");
  const rows = (data && data[view]) || [];
  const labelKey = view === "makes" ? "make" : "model";

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 12),
      labelKey,
      valueKey: "revenue",
      color: PALETTE[2],
      tooltip: (r) =>
        `Revenue <b>${money(r.revenue)}</b><br/>Margin <b>${pct(
          r.margin_pct
        )}</b><br/><span style="color:#8b94a7">${num(r.units)} units &middot; ASP ${money(
          r.avg_price
        )}</span>`,
    });
  }, [data, view]);

  return (
    <Panel
      title="Makes & Models"
      right={
        <Segmented
          value={view}
          options={[
            { key: "makes", label: "Make" },
            { key: "models", label: "Model" },
          ]}
          onChange={setView}
        />
      }
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={300}
            onClick={(p) => onDrill(view === "makes" ? { make: p.name } : { search: p.name })}
          />
          {view === "makes" && (
            <Caveat>
              UnitBase.Make is free text and holds several casings of the same brand, so
              makes are grouped case-insensitively. 1,008 units have no make recorded.
            </Caveat>
          )}
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function UnitsAgingPanel({ data, loading, error, asOf, onDrill }) {
  const rows = (data && data.aging) || [];
  const total = rows.reduce((a, r) => a + r.units, 0);

  const option = useMemo(() => {
    if (!rows.length) return null;
    return {
      animationDuration: 350,
      grid: { top: 30, left: 52, right: 56, bottom: 30 },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.bucket === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Units <b>${num(r.units)}</b><br/>Recorded cost <b>${money(r.cost)}</b>`;
        },
      },
      xAxis: catAxis(rows.map((r) => r.bucket), 0),
      yAxis: [valAxis("Units"), { ...valAxis("Cost", (v) => compactMoney(v)), splitLine: { show: false } }],
      series: [
        {
          name: "Units",
          type: "bar",
          barMaxWidth: 40,
          data: rows.map((r, i) => ({
            value: r.units,
            itemStyle: { color: RAMP[Math.min(i, RAMP.length - 1)], borderRadius: [3, 3, 0, 0] },
          })),
        },
        {
          name: "Recorded cost",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: "#e6e9ef" },
          itemStyle: { color: "#e6e9ef" },
          data: rows.map((r) => +(r.cost || 0).toFixed(2)),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Inventory On Hand & Aging" hint={`snapshot as of ${asOf || "-"}`}>
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={236} onClick={(p) => onDrill({ bucket: p.name })} />
          <Caveat>
            {num(total)} units with StockStatus 'instock' and IsActive set. DateReceived is
            populated on only 71 of them, so age falls back to the purchase date and then to
            the record's creation date; BaseCost is recorded on 172, so the cost line is a
            floor rather than a full valuation.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function UnitsTradeInPanel({ data, loading, error }) {
  const rows = (data && data.trade_ins) || [];
  const totals = rows.reduce(
    (a, r) => ({
      trade_ins: a.trade_ins + r.trade_ins,
      allowance: a.allowance + r.allowance,
      over_allowance: a.over_allowance + r.over_allowance,
      inventory_value: a.inventory_value + r.inventory_value,
    }),
    { trade_ins: 0, allowance: 0, over_allowance: 0, inventory_value: 0 }
  );

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r.category);
    return {
      animationDuration: 350,
      grid: { top: 30, left: 60, right: 16, bottom: 30 },
      legend: legendStyle,
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, ...TOOLTIP_STYLE,
        formatter: (ps) =>
          `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>` +
          ps
            .map(
              (x) =>
                `<div style="display:flex;justify-content:space-between;gap:16px"><span>${x.marker}${x.seriesName}</span><b>${compactMoney(
                  x.value
                )}</b></div>`
            )
            .join(""),
      },
      xAxis: catAxis(labels, 0),
      yAxis: valAxis(null, (v) => compactMoney(v)),
      series: [
        {
          name: "Inventory value",
          type: "bar",
          stack: "t",
          barMaxWidth: 40,
          itemStyle: { color: PALETTE[0] },
          data: rows.map((r) => +(r.inventory_value || 0).toFixed(2)),
        },
        {
          name: "Over-allowance",
          type: "bar",
          stack: "t",
          barMaxWidth: 40,
          itemStyle: { color: "#e8615d" },
          data: rows.map((r) => +(r.over_allowance || 0).toFixed(2)),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Trade-In Exposure" hint="not netted against revenue">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <div className="stat-row">
            <div className="stat">
              <div className="l">Trade-Ins</div>
              <div className="v">{num(totals.trade_ins)}</div>
            </div>
            <div className="stat" title="Total allowance granted on the invoices">
              <div className="l">Allowance</div>
              <div className="v">{compactMoney(totals.allowance)}</div>
            </div>
            <div className="stat" title="Value booked into inventory">
              <div className="l">Inventory Value</div>
              <div className="v">{compactMoney(totals.inventory_value)}</div>
            </div>
            <div className="stat" title="Allowance granted above the appraised value">
              <div className="l">Over-Allowance</div>
              <div className="v">{compactMoney(totals.over_allowance)}</div>
            </div>
          </div>
          <EChart option={option} height={206} />
          <Caveat>
            Trade-ins are inventory acquired, not negative revenue. Netting them into unit
            sales would report Sales at roughly zero margin, so they are tracked here
            instead. Over-allowance is the part of the allowance above appraised value.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function UnitsSalespeoplePanel({ data, loading, error, onDrill }) {
  const rows = (data && data.salespeople) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 12),
      labelKey: "name",
      valueKey: "revenue",
      color: PALETTE[1],
      tooltip: (r) =>
        `Revenue <b>${money(r.revenue)}</b><br/>Gross profit <b>${money(
          r.gross_profit
        )}</b><br/>Margin <b>${pct(r.margin_pct)}</b><br/><span style="color:#8b94a7">${num(
          r.units
        )} units &middot; ${num(r.new_units)} new &middot; ${num(
          r.customers
        )} customers</span>`,
    });
  }, [data]);

  return (
    <Panel title="Salesperson Detail" hint="unit sales only, click to filter">
      <Body loading={loading} error={error} empty={!rows.length}>
        <EChart option={option} height={300} onClick={(p) => onDrill({ salesperson: p.name })} />
      </Body>
    </Panel>
  );
}

function UnitsTimelinePanel({ data, loading, error, grain, onGrain }) {
  const rows = (data && data.timeline) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r.period);
    return {
      animationDuration: 350,
      grid: { top: 32, left: 48, right: 62, bottom: labels.length > 18 ? 44 : 30 },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.period === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            New <b>${num(r.new_units)}</b><br/>Used <b>${num(r.used_units)}</b><br/>
            Revenue <b>${compactMoney(r.revenue)}</b><br/>ASP <b>${money(r.avg_price)}</b><br/>
            Margin <b>${pct(r.margin_pct)}</b>`;
        },
      },
      xAxis: catAxis(labels),
      yAxis: [
        valAxis("Units"),
        { ...valAxis("ASP", (v) => compactMoney(v)), splitLine: { show: false } },
      ],
      series: [
        {
          name: "New",
          type: "bar",
          stack: "u",
          barMaxWidth: 30,
          itemStyle: { color: "#4c9be8" },
          data: rows.map((r) => r.new_units),
        },
        {
          name: "Used",
          type: "bar",
          stack: "u",
          barMaxWidth: 30,
          itemStyle: { color: "#f7b32b" },
          data: rows.map((r) => r.used_units),
        },
        {
          name: "Avg selling price",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: "#e6e9ef" },
          itemStyle: { color: "#e6e9ef" },
          data: rows.map((r) => (r.avg_price == null ? null : +r.avg_price.toFixed(2))),
        },
      ],
      dataZoom: labels.length > 24 ? [{ type: "inside" }] : undefined,
    };
  }, [data]);

  return (
    <Panel
      title="Unit Volume & Average Selling Price"
      right={<Segmented value={grain} options={GRAINS} onChange={onGrain} />}
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <EChart option={option} height={260} />
      </Body>
    </Panel>
  );
}

/* ------------------------------------------------------------------- tab */

const UNIT_SALE_COLUMNS = [
  col.date("activity_date", "Date"),
  col.text("doc_no", "Doc"),
  col.text("stock_no", "Stock"),
  col.text("make", "Make"),
  col.text("model", "Model", { render: (r) => <span className="name">{text(r.model)}</span> }),
  col.num("model_year", "Yr"),
  col.text("category", "Category"),
  col.text("condition_class", "Cond"),
  col.text("customer_name", "Customer", {
    render: (r) => <span className="name">{text(r.customer_name)}</span>,
  }),
  col.text("salesperson", "Salesperson"),
  col.num("qty", "Qty"),
  col.exact("revenue", "Revenue"),
  col.exact("cost", "Cost"),
  col.exact("gross_profit", "GP"),
  col.pct("margin_pct", "GM %"),
];

const UNIT_INVENTORY_COLUMNS = [
  col.text("stock_no", "Stock"),
  col.text("make", "Make"),
  col.text("model", "Model", { render: (r) => <span className="name">{text(r.model)}</span> }),
  col.num("model_year", "Yr"),
  col.text("category", "Category"),
  col.text("condition_grade", "Grade"),
  col.date("received", "On Hand Since"),
  col.text("age_basis", "Age Basis", { hint: "Which date the age was measured from" }),
  col.num("age_days", "Days", 0, {
    hint: "Days on hand at the end of the selected window",
  }),
  col.exact("cost", "Cost"),
  col.exact("retail", "Retail"),
];

function UnitsTab({ params, grain, onGrain, initialFilters }) {
  const charts = useEndpoint("/api/units/charts", params ? { ...params, grain } : null);
  const summary = useEndpoint("/api/units/summary", params);
  const [saleFilters, setSaleFilters] = useState(initialFilters || {});
  const [invFilters, setInvFilters] = useState({});

  useEffect(() => {
    if (initialFilters) setSaleFilters(initialFilters);
  }, [JSON.stringify(initialFilters || {})]);

  const asOf = summary.data && summary.data.on_hand_as_of;
  const shared = { data: charts.data, loading: charts.loading, error: charts.error };

  return (
    <React.Fragment>
      <UnitsKpis params={params} />

      <div className="grid g-3">
        <UnitsCategoryPanel {...shared} onDrill={setSaleFilters} />
        <UnitsConditionPanel {...shared} onDrill={setSaleFilters} />
        <UnitsMakesPanel {...shared} onDrill={setSaleFilters} />
      </div>

      <div className="grid g-2">
        <UnitsTimelinePanel {...shared} grain={grain} onGrain={onGrain} />
        <UnitsSalespeoplePanel {...shared} onDrill={setSaleFilters} />
      </div>

      <div className="grid g-2">
        <UnitsAgingPanel {...shared} asOf={asOf} onDrill={setInvFilters} />
        <UnitsTradeInPanel {...shared} />
      </div>

      <Panel title="Unit Sale Lines" hint="every booked UN line in range">
        <DataTable
          path="/api/units/detail"
          params={params}
          columns={UNIT_SALE_COLUMNS}
          filters={saleFilters}
          onFilters={setSaleFilters}
          defaultSort="activity_date"
          searchHint="Search stock no, model, customer, doc"
        />
      </Panel>

      <Panel title="Inventory On Hand" hint={`snapshot as of ${asOf || "-"}`}>
        <DataTable
          path="/api/units/inventory"
          params={params}
          columns={UNIT_INVENTORY_COLUMNS}
          filters={invFilters}
          onFilters={setInvFilters}
          defaultSort="age_days"
          searchHint="Search stock no, make, model"
          note="Cost and retail are blank where UnitBase never recorded them, which is most attachments."
        />
      </Panel>
    </React.Fragment>
  );
}
