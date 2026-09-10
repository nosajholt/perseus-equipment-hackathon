/* Rental tab.
 *
 * Rental has no cost basis: DepreciationAmt is 0 on all 22,995 rental lines
 * and every RentalGroup.DepreciationPct is 0, so nothing here is a margin.
 * Utilisation is measured against units that have actually rented, not the
 * UnitBase.Rental flag, which is stale on most of the fleet.
 */

function RentalKpis({ params }) {
  const { loading, data, error } = useEndpoint("/api/rental/summary", params);
  const d = data || {};

  const cards = [
    { label: "Rental Revenue", value: compactMoney(d.revenue), note: "no cost basis" },
    { label: "Units Rented", value: num(d.units_rented), note: `${num(d.lines)} rental lines` },
    {
      label: "Revenue / Unit",
      value: money(d.revenue_per_unit),
      note: `${num(d.turns_per_unit, 1)} turns each`,
    },
    {
      label: "Billed Days",
      value: num(d.billed_days),
      note: `${money(d.revenue_per_day)} per day`,
    },
    {
      label: "Fleet Utilisation",
      value: pct(d.utilisation_pct),
      note: `${num(d.active_in_window)} of ${num(d.fleet_ever_rented)} ever-rented units`,
    },
    {
      label: "Idle Units",
      value: num(d.idle_in_window),
      note: "history but no rental in range",
      tone: d.idle_in_window > 0 ? "warn" : null,
    },
    {
      label: "Meter Hours",
      value: compactNum(d.meter_used),
      note: `${num(d.lines_with_meter)} lines report a meter`,
    },
    {
      label: "Contracts",
      value: num(d.contracts),
      note: `${num(d.customers)} customers, ${compactMoney(d.discount)} discount`,
    },
  ];

  return <KpiGrid loading={loading} error={error} cards={cards} />;
}

function RentalUtilisationPanel({ data, loading, error, grain, onGrain }) {
  const rows = (data && data.utilisation) || [];
  const [mode, setMode] = useState("units");

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r.period);
    const byUnits = mode === "units";
    return {
      animationDuration: 350,
      grid: { top: 32, left: 52, right: 62, bottom: labels.length > 18 ? 44 : 30 },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.period === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Distinct units on rent <b>${num(r.units_on_rent)}</b><br/>
            Rental lines <b>${num(r.lines)}</b><br/>Billed days <b>${num(
            r.billed_days
          )}</b><br/>Revenue <b>${compactMoney(r.revenue)}</b><br/>
            Revenue per unit <b>${money(r.revenue_per_unit)}</b><br/>
            Revenue per billed day <b>${money(r.revenue_per_day)}</b>`;
        },
      },
      xAxis: catAxis(labels),
      yAxis: [
        valAxis(byUnits ? "Units on rent" : "Billed days"),
        { ...valAxis("Revenue", (v) => compactMoney(v)), splitLine: { show: false } },
      ],
      series: [
        {
          name: byUnits ? "Units on rent" : "Billed days",
          type: "bar",
          barMaxWidth: 30,
          itemStyle: { color: PALETTE[3], borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) =>
            byUnits ? r.units_on_rent : +(r.billed_days || 0).toFixed(0)
          ),
        },
        {
          name: "Revenue",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2, color: "#4ec9a5" },
          data: rows.map((r) => +(r.revenue || 0).toFixed(2)),
        },
      ],
      dataZoom: labels.length > 24 ? [{ type: "inside" }] : undefined,
    };
  }, [data, mode]);

  return (
    <Panel
      title="Fleet Activity Over Time"
      right={
        <div className="head-controls">
          <Segmented
            value={mode}
            options={[
              { key: "units", label: "Units" },
              { key: "days", label: "Billed days" },
            ]}
            onChange={setMode}
          />
          <Segmented value={grain} options={GRAINS} onChange={onGrain} />
        </div>
      }
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={260} />
          <Caveat>
            Units on rent counts distinct units billed in each period, not
            simultaneous occupancy: a unit rented three times in a month counts once.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function RentalGroupPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.groups) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 12),
      labelKey: "rental_group",
      valueKey: "revenue",
      color: (r) => (r.rental_group === "Ungrouped" ? "#6b7488" : PALETTE[3]),
      tooltip: (r) =>
        `Revenue <b>${compactMoney(r.revenue)}</b><br/>Units <b>${num(
          r.units
        )}</b><br/>Rentals <b>${num(r.lines)}</b><br/>Billed days <b>${num(
          r.billed_days
        )}</b><br/>Per day <b>${money(r.revenue_per_day)}</b>`,
    });
  }, [data]);

  return (
    <Panel title="Revenue by Rental Group">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={250}
            onClick={(p) => onDrill({ rental_group: p.name })}
          />
          <Caveat>
            RentalGroupId lives on UnitBase and is only set on the 126 units flagged
            Rental=1, so everything rented outside that flagged fleet lands in
            Ungrouped rather than being missing.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function RentalContractPanel({ data, loading, error, onDrill }) {
  const [dim, setDim] = useState("contract_status");
  const rows = (data && (dim === "contract_status" ? data.contract_status : data.durations)) || [];
  const labelKey = dim === "contract_status" ? "contract_status" : "duration_unit";

  const option = useMemo(() => {
    if (!rows.length) return null;
    return donutOption({
      rows,
      labelKey,
      valueKey: "revenue",
      caption: "revenue",
      colors: PALETTE,
    });
  }, [data, dim]);

  return (
    <Panel
      title="Contract & Duration Mix"
      right={
        <Segmented
          value={dim}
          options={[
            { key: "contract_status", label: "Contract" },
            { key: "duration_unit", label: "Duration" },
          ]}
          onChange={setDim}
        />
      }
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={224}
            onClick={(p) => onDrill({ [labelKey]: p.name })}
          />
          <table>
            <thead>
              <tr>
                <th>{dim === "contract_status" ? "Contract Status" : "Billed As"}</th>
                <th className="num">Lines</th>
                <th className="num">Revenue</th>
                <th className="num">Per Day</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 6).map((r) => (
                <tr
                  key={r[labelKey]}
                  className="rowlink"
                  onClick={() => onDrill({ [labelKey]: r[labelKey] })}
                >
                  <td>{text(r[labelKey])}</td>
                  <td className="num">{num(r.lines)}</td>
                  <td className="num">{compactMoney(r.revenue)}</td>
                  <td className="num">{money(r.revenue_per_day)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Caveat>
            'none' means the invoice has no RentalContract row at all - only 12,495
            of the rental invoices carry one, so contract status covers part of the
            book rather than all of it.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function RentalTopUnitsPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.top_units) || [];
  const [metric, setMetric] = useState("revenue");

  const option = useMemo(() => {
    if (!rows.length) return null;
    const ranked = rows
      .slice()
      .sort((a, b) => (b[metric] || 0) - (a[metric] || 0))
      .slice(0, 12);
    return rankedBarOption({
      rows: ranked,
      labelKey: "stock_no",
      valueKey: metric,
      format: metric === "revenue" ? compactMoney : (v) => num(v),
      color: PALETTE[5],
      tooltip: (r) =>
        `<span style="color:#8b94a7">${text(r.model)} - ${text(r.rental_group)}</span><br/>
         Revenue <b>${compactMoney(r.revenue)}</b><br/>Rentals <b>${num(
          r.rentals
        )}</b><br/>Billed days <b>${num(r.billed_days)}</b><br/>
         Meter hours <b>${num(r.meter_used)}</b><br/>Per day <b>${money(
          r.revenue_per_day
        )}</b>`,
    });
  }, [data, metric]);

  return (
    <Panel
      title="Top Units"
      right={
        <Segmented
          value={metric}
          options={[
            { key: "revenue", label: "Revenue" },
            { key: "billed_days", label: "Days" },
            { key: "meter_used", label: "Meter" },
          ]}
          onChange={setMetric}
        />
      }
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <EChart option={option} height={280} onClick={(p) => onDrill({ stock_no: p.name })} />
      </Body>
    </Panel>
  );
}

function RentalIdlePanel({ data, loading, error, asOf, onDrill }) {
  const rows = (data && data.idle) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return {
      animationDuration: 350,
      grid: { top: 30, left: 12, right: 56, bottom: 40, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.bucket === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Units <b>${num(r.units)}</b><br/>Lifetime revenue <b>${compactMoney(
            r.revenue_all_time
          )}</b><br/>
            <span style="color:#8b94a7">${num(r.flagged)} still flagged Rental=1</span>`;
        },
      },
      xAxis: catAxis(rows.map((r) => r.bucket), 22),
      yAxis: [
        valAxis("Units"),
        { ...valAxis("Lifetime rev", (v) => compactMoney(v)), splitLine: { show: false } },
      ],
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
          name: "Lifetime revenue",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: "#e6e9ef" },
          itemStyle: { color: "#e6e9ef" },
          data: rows.map((r) => +(r.revenue_all_time || 0).toFixed(2)),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Idle Fleet" hint={`idle as of ${asOf || "-"}`}>
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={252} onClick={(p) => onDrill({ bucket: p.name })} />
          <Caveat>
            The fleet here is every unit that has ever rented (680 of them), not the
            126 carrying UnitBase.Rental=1. Idle days run from the last rental to the
            end of the selected window, so a unit sold years ago still shows as idle
            rather than retired - StockStatus in the table below tells the two apart.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

/* ------------------------------------------------------------------- tab */

const RENTAL_LINE_COLUMNS = [
  col.date("activity_date", "Date"),
  col.text("doc_no", "Invoice"),
  col.text("stock_no", "Stock #"),
  col.text("model", "Model", { render: (r) => <span className="name">{text(r.model)}</span> }),
  col.text("rental_group", "Group"),
  col.text("customer_name", "Customer", {
    render: (r) => <span className="name">{text(r.customer_name)}</span>,
  }),
  col.text("contract_status", "Contract"),
  col.text("duration_unit", "Billed As"),
  col.num("billed_days", "Billed Days", 1),
  col.num("span_days", "Span Days", 0, {
    hint: "Calendar days between StartDate and EndDate, sortable",
  }),
  col.date("start_date", "Start"),
  col.date("end_date", "End"),
  col.exact("rate", "Rate"),
  col.num("meter_used", "Meter"),
  col.exact("revenue", "Revenue"),
];

const RENTAL_FLEET_COLUMNS = [
  col.text("stock_no", "Stock #"),
  col.text("make", "Make"),
  col.text("model", "Model", { render: (r) => <span className="name">{text(r.model)}</span> }),
  col.num("model_year", "Year"),
  col.text("rental_group", "Group"),
  col.text("stock_status", "Stock Status"),
  col.date("last_rented", "Last Rented"),
  col.num("idle_days", "Idle Days"),
  col.num("rentals_in_window", "Rentals (range)"),
  col.money("revenue_in_window", "Revenue (range)"),
  col.num("billed_days", "Billed Days", 1),
  col.num("meter_used", "Meter"),
  col.num("rentals_all_time", "Rentals (all)"),
  col.money("revenue_all_time", "Revenue (all)"),
];

function RentalTab({ params, grain, onGrain, initialFilters }) {
  const charts = useEndpoint("/api/rental/charts", params ? { ...params, grain } : null);
  const summary = useEndpoint("/api/rental/summary", params);
  const [lineFilters, setLineFilters] = useState(initialFilters || {});
  const [fleetFilters, setFleetFilters] = useState({});

  useEffect(() => {
    if (initialFilters) setLineFilters(initialFilters);
  }, [JSON.stringify(initialFilters || {})]);

  const asOf = summary.data && summary.data.as_of;
  const shared = { data: charts.data, loading: charts.loading, error: charts.error };

  return (
    <React.Fragment>
      <RentalKpis params={params} />

      <div className="grid g-2">
        <RentalUtilisationPanel {...shared} grain={grain} onGrain={onGrain} />
        <RentalTopUnitsPanel {...shared} onDrill={setLineFilters} />
      </div>

      <div className="grid g-3">
        <RentalGroupPanel {...shared} onDrill={setLineFilters} />
        <RentalContractPanel {...shared} onDrill={setLineFilters} />
        <RentalIdlePanel {...shared} asOf={asOf} onDrill={setFleetFilters} />
      </div>

      <Panel title="Rental Lines" hint="every booked RU line in range">
        <DataTable
          path="/api/rental/detail"
          params={params}
          columns={RENTAL_LINE_COLUMNS}
          filters={lineFilters}
          onFilters={setLineFilters}
          defaultSort="revenue"
          searchHint="Search stock #, model, customer, contract"
          note="Duration is derived from StartDate, EndDate and DurationQty. ReturnDate is NULL on 19,930 of 22,995 rental lines, so it is shown but never used to compute days."
        />
      </Panel>

      <Panel title="Rental Fleet" hint={`per-unit history as of ${asOf || "-"}`}>
        <DataTable
          path="/api/rental/fleet"
          params={params}
          columns={RENTAL_FLEET_COLUMNS}
          filters={fleetFilters}
          onFilters={setFleetFilters}
          defaultSort="revenue_in_window"
          searchHint="Search stock #, make, model"
          note="Every unit with rental history plus every unit flagged Rental=1. All-time columns ignore the start of the date filter so a unit's full history stays visible."
        />
      </Panel>
    </React.Fragment>
  );
}
