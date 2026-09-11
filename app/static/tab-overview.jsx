/* Executive Overview - the default landing tab.
 *
 * Company-wide revenue, margin and department mix. Everything here is defined
 * by the semantic layer: revenue counts finalized and archived invoices only,
 * and gross margin covers Units and Parts, the only lines with a cost basis.
 */

function OverviewKpis({ params }) {
  const { loading, data, error } = useEndpoint("/api/kpis", params);

  if (loading)
    return (
      <div className="kpis">
        <div className="kpi">
          <Loading />
        </div>
      </div>
    );
  if (error) return <Err msg={error} />;

  const sp = data.sparklines || {};
  const deltas = (m) => [
    { key: "vs prev", value: m.prior_period_pct },
    { key: "vs yr", value: m.prior_year_pct },
  ];

  const cards = [
    { label: "Revenue", value: compactMoney(data.revenue.value), m: data.revenue, spark: sp.revenue },
    {
      label: "Gross Profit",
      value: compactMoney(data.gross_profit.value),
      m: data.gross_profit,
      spark: sp.gross_profit,
      note: "Units + Parts only",
    },
    {
      label: "Gross Margin",
      value: pct(data.gross_margin_pct.value),
      m: data.gross_margin_pct,
      spark: sp.gross_margin_pct,
      note: "of costed revenue",
    },
    { label: "Invoices", value: num(data.invoices.value), m: data.invoices, spark: sp.invoices },
    {
      label: "Avg Ticket",
      value: money(data.avg_ticket.value),
      m: data.avg_ticket,
      spark: sp.avg_ticket,
    },
    { label: "Units Sold", value: num(data.units_sold.value), m: data.units_sold, spark: sp.units_sold },
    {
      label: "Labor Hours",
      value: num(data.labor_hours.value),
      m: data.labor_hours,
      spark: sp.labor_hours,
    },
    {
      label: "Trade-In Allowance",
      value: compactMoney(data.trade_in_allowance.value),
      m: data.trade_in_allowance,
      spark: sp.trade_in_allowance,
      note: "excluded from revenue",
    },
  ];

  return (
    <div className="kpis">
      {cards.map((c) => (
        <KpiCard
          key={c.label}
          label={c.label}
          value={c.value}
          deltas={deltas(c.m)}
          spark={c.spark}
          note={c.note}
        />
      ))}
    </div>
  );
}

/* ----------------------------------------------------------- trend + mix */

function TrendPanel({ params, grain, onGrain, onDrill }) {
  const { loading, data, error } = useEndpoint("/api/trend", { ...params, grain });

  const option = useMemo(() => {
    if (!data || !data.periods) return null;
    const periods = data.periods;
    const labels = periods.map((p) => p.period);

    const series = DEPTS.map((dp) => ({
      name: dp.key,
      type: "bar",
      stack: "rev",
      barMaxWidth: 34,
      itemStyle: { color: dp.color },
      data: periods.map((p) => ({
        value: +(p.revenue[dp.key] || 0).toFixed(2),
        // Partial periods are drawn faded and outlined so a short final period
        // never reads as a genuine collapse in revenue.
        itemStyle: p.partial
          ? { color: dp.color, opacity: 0.35, borderColor: dp.color, borderWidth: 1 }
          : undefined,
      })),
    }));

    series.push({
      name: "Gross Margin %",
      type: "line",
      yAxisIndex: 1,
      smooth: true,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { width: 2, color: "#e6e9ef" },
      itemStyle: { color: "#e6e9ef" },
      // Margin is over costed revenue only, matching the KPI definition.
      data: periods.map((p) =>
        p.totals.gross_margin_pct == null ? null : +p.totals.gross_margin_pct.toFixed(2)
      ),
    });

    return {
      animationDuration: 350,
      grid: { top: 42, left: 64, right: 54, bottom: 46 },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          if (!ps.length) return "";
          const p = periods.find((x) => x.period === ps[0].axisValue);
          let total = 0;
          const rows = ps
            .filter((x) => x.seriesName !== "Gross Margin %")
            .map((x) => {
              total += x.value || 0;
              return `<div style="display:flex;justify-content:space-between;gap:18px">
                        <span>${x.marker}${x.seriesName}</span>
                        <b>${compactMoney(x.value)}</b></div>`;
            })
            .join("");
          const gm = ps.find((x) => x.seriesName === "Gross Margin %");
          return `<div style="font-weight:650;margin-bottom:5px">${ps[0].axisValue}${
            p && p.partial ? ' <span style="color:#f0a33a">(partial period)</span>' : ""
          }</div>${rows}
          <div style="border-top:1px solid #333c50;margin-top:5px;padding-top:5px;display:flex;justify-content:space-between;gap:18px">
            <span>Total</span><b>${compactMoney(total)}</b></div>
          ${
            gm && gm.value != null
              ? `<div style="display:flex;justify-content:space-between;gap:18px"><span>Gross Margin</span><b>${pct(
                  gm.value
                )}</b></div>`
              : ""
          }
          ${
            p && p.totals.trade_in_allowance
              ? `<div style="display:flex;justify-content:space-between;gap:18px;color:#8b94a7"><span>Trade-in allowance</span><span>${compactMoney(
                  p.totals.trade_in_allowance
                )}</span></div>`
              : ""
          }`;
        },
      },
      xAxis: catAxis(labels),
      yAxis: [
        valAxis("Revenue", (v) => compactMoney(v)),
        {
          ...valAxis("GM %", "{value}%"),
          splitLine: { show: false },
        },
      ],
      dataZoom:
        labels.length > 18
          ? [
              { type: "inside" },
              {
                type: "slider",
                height: 14,
                bottom: 6,
                borderColor: AXIS_LINE,
                textStyle: { color: "#8b94a7", fontSize: 9 },
              },
            ]
          : undefined,
      series,
    };
  }, [data]);

  return (
    <Panel
      title="Revenue & Gross Margin Trend"
      hint={onDrill ? "click a bar for that period's invoices" : undefined}
      right={<Segmented value={grain} options={GRAINS} onChange={onGrain} />}
    >
      {loading ? (
        <Loading />
      ) : error ? (
        <Err msg={error} />
      ) : (
        <EChart
          option={option}
          height={356}
          // The bar's own label is the period the register filters on, so the
          // drill-down works the same at every grain without recomputing dates.
          onClick={onDrill ? (p) => onDrill({ period: p.name }) : undefined}
        />
      )}
    </Panel>
  );
}

function MixPanel({ params }) {
  const { loading, data, error } = useEndpoint("/api/mix", params);

  const option = useMemo(() => {
    if (!data || !data.departments) return null;

    const revRows = data.departments.filter((r) => r.revenue > 0);
    // The gross profit donut only includes departments that have a cost basis,
    // so Service and Rental are not implied to run at 100% margin.
    const gpRows = data.departments.filter((r) => r.has_cost_basis && r.gross_profit > 0);

    const donut = (name, rows, field, center) => ({
      name,
      type: "pie",
      radius: ["40%", "60%"],
      center,
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "#161b26", borderWidth: 2 },
      label: {
        color: "#8b94a7",
        fontSize: 10.5,
        formatter: (p) => `${p.name}\n${p.percent.toFixed(0)}%`,
      },
      labelLine: { length: 5, length2: 6, lineStyle: { color: "#333c50" } },
      data: rows.map((r) => ({
        name: r.department,
        value: +Math.max(r[field], 0).toFixed(2),
        itemStyle: { color: DEPT_COLOR[r.department] },
      })),
    });

    const label = (text, left) => ({
      text,
      left,
      top: "45%",
      textAlign: "center",
      textStyle: { color: "#8b94a7", fontSize: 11, fontWeight: 500 },
    });

    return {
      animationDuration: 350,
      tooltip: {
        trigger: "item",
        ...TOOLTIP_STYLE,
        formatter: (p) => `${p.marker}${p.name}<br/><b>${compactMoney(p.value)}</b> (${p.percent}%)`,
      },
      title: [label("Revenue", "25%"), label("Gross Profit", "75%")],
      series: [
        donut("Revenue", revRows, "revenue", ["25%", "50%"]),
        donut("Gross Profit", gpRows, "gross_profit", ["75%", "50%"]),
      ],
    };
  }, [data]);

  return (
    <Panel title="Department Mix" hint="revenue vs gross profit share">
      {loading ? (
        <Loading />
      ) : error ? (
        <Err msg={error} />
      ) : (
        <React.Fragment>
          <EChart option={option} height={230} />
          <table>
            <thead>
              <tr>
                <th>Department</th>
                <th className="num">Revenue</th>
                <th className="num">Gross Profit</th>
                <th className="num">GM %</th>
              </tr>
            </thead>
            <tbody>
              {data.departments.map((r) => (
                <tr key={r.department}>
                  <td>
                    <span
                      className="sw"
                      style={{
                        background: DEPT_COLOR[r.department],
                        display: "inline-block",
                        width: 9,
                        height: 9,
                        borderRadius: 2,
                        marginRight: 6,
                      }}
                    />
                    {r.department}
                  </td>
                  <td className="num">{compactMoney(r.revenue)}</td>
                  <td className="num">
                    {r.has_cost_basis ? (
                      compactMoney(r.gross_profit)
                    ) : (
                      <span style={{ color: "var(--muted)" }}>no cost basis</span>
                    )}
                  </td>
                  <td className="num">{r.margin_pct == null ? "-" : pct(r.margin_pct)}</td>
                </tr>
              ))}
              {data.trade_in_allowance > 0 && (
                <tr>
                  <td style={{ color: "var(--muted)", fontStyle: "italic" }}>
                    Trade-in allowance
                  </td>
                  <td className="num" style={{ color: "var(--muted)" }}>
                    ({compactMoney(data.trade_in_allowance)})
                  </td>
                  <td className="num" style={{ color: "var(--muted)" }} colSpan={2}>
                    excluded from revenue
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </React.Fragment>
      )}
    </Panel>
  );
}

/* --------------------------------------------------------- bottom panels */

function TopCustomersPanel({ params, onOpen }) {
  const { loading, data, error } = useEndpoint("/api/top-customers", { ...params, limit: 10 });
  const rows = (data && data.customers) || [];
  const max = rows.reduce((a, r) => Math.max(a, r.revenue), 0) || 1;

  return (
    <Panel
      title="Top Customers"
      hint={onOpen ? "click a row for the full account list" : "by revenue"}
    >
      <Body loading={loading} error={error} empty={rows.length === 0} emptyMsg="No customers in this range">
        <table>
          <thead>
            <tr>
              <th className="rank">#</th>
              <th>Customer</th>
              <th className="num">Revenue</th>
              <th className="num">GP</th>
              <th className="num">Inv</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={r.customer_id ?? i}
                title={`${r.customer_name} (${r.customer_no})`}
                className={onOpen ? "rowlink" : ""}
                onClick={() => onOpen && onOpen(r)}
              >
                <td className="rank">{i + 1}</td>
                <td className="name">{r.customer_name}</td>
                <td className="num">
                  {compactMoney(r.revenue)}
                  <div
                    className="minibar"
                    style={{ width: `${(r.revenue / max) * 100}%`, marginTop: 3 }}
                  />
                </td>
                <td className="num">{compactMoney(r.gross_profit)}</td>
                <td className="num">{num(r.invoices)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Body>
    </Panel>
  );
}

function TopSalespeoplePanel({ params, onOpen }) {
  const { loading, data, error } = useEndpoint("/api/top-salespeople", { ...params, limit: 10 });
  const rows = (data && data.salespeople) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 10),
      labelKey: "name",
      valueKey: "revenue",
      color: PALETTE[0],
      tooltip: (r) =>
        `Revenue <b>${money(r.revenue)}</b><br/>Gross profit <b>${money(
          r.gross_profit
        )}</b><br/><span style="color:#8b94a7">${num(r.invoices)} invoices &middot; ${num(
          r.units_sold
        )} units</span>`,
    });
  }, [data]);

  const onClick = useCallback(
    (p) => {
      if (onOpen) onOpen(p.name);
    },
    [onOpen]
  );

  return (
    <Panel title="Salesperson Performance" hint="by revenue">
      <Body loading={loading} error={error} empty={rows.length === 0}>
        <EChart option={option} height={300} onClick={onOpen ? onClick : undefined} />
      </Body>
    </Panel>
  );
}

function ServiceGaugePanel({ params }) {
  const { loading, data, error } = useEndpoint("/api/service", params);

  const option = useMemo(() => {
    if (!data) return null;
    const v = data.utilization_pct;
    // Billed hours can legitimately exceed clocked hours (flat-rate work), so the
    // scale grows rather than pinning the needle at a misleading 100%.
    const max = Math.max(100, Math.ceil((v || 0) / 20) * 20);
    return {
      animationDuration: 350,
      series: [
        {
          type: "gauge",
          startAngle: 200,
          endAngle: -20,
          min: 0,
          max,
          radius: "96%",
          center: ["50%", "62%"],
          progress: {
            show: true,
            width: 13,
            itemStyle: { color: v != null && v >= 100 ? "#f7b32b" : "#4ec9a5" },
          },
          axisLine: { lineStyle: { width: 13, color: [[1, "#232b3a"]] } },
          splitNumber: 5,
          axisTick: { show: false },
          splitLine: { length: 8, lineStyle: { color: "#333c50", width: 1 } },
          axisLabel: { color: "#8b94a7", fontSize: 9, distance: 12 },
          pointer: { show: false },
          anchor: { show: false },
          title: { show: false },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, "-8%"],
            fontSize: 26,
            fontWeight: 600,
            color: "#e6e9ef",
            formatter: (x) => (x == null ? "-" : x.toFixed(1) + "%"),
          },
          data: [{ value: v }],
        },
      ],
    };
  }, [data]);

  return (
    <Panel
      title="Service Utilization"
      hint={
        data && data.utilization_pct >= 100
          ? "billed exceeds clocked (flat rate)"
          : "billed vs clocked hours"
      }
    >
      <Body loading={loading} error={error}>
        <React.Fragment>
          <EChart option={option} height={140} />
          <div className="stat-row">
            <div className="stat" title="Technician time clocked in WorkInProgress">
              <div className="l">Clocked Hrs</div>
              <div className="v">{num(data && data.clock_hours)}</div>
            </div>
            <div className="stat" title="Actual hours billed on invoice segments">
              <div className="l">Billed Hrs</div>
              <div className="v">{num(data && data.billed_hours)}</div>
            </div>
            <div className="stat" title="Labor revenue divided by billed hours">
              <div className="l">Eff. Rate</div>
              <div className="v">{money(data && data.effective_rate)}</div>
            </div>
            <div className="stat">
              <div className="l">Labor Rev</div>
              <div className="v">{compactMoney(data && data.labor_revenue)}</div>
            </div>
            <div className="stat">
              <div className="l">Work Orders</div>
              <div className="v">{num(data && data.work_orders)}</div>
            </div>
            <div className="stat">
              <div className="l">Techs</div>
              <div className="v">{num(data && data.techs_active)}</div>
            </div>
          </div>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function RentalSummaryPanel({ params }) {
  const { loading, data, error } = useEndpoint("/api/rental", params);

  return (
    <Panel title="Rental Fleet" hint="revenue only, no cost basis">
      <Body loading={loading} error={error}>
        <React.Fragment>
          <div className="stat-row">
            <div className="stat">
              <div className="l">Rental Rev</div>
              <div className="v">{compactMoney(data && data.revenue)}</div>
            </div>
            <div className="stat">
              <div className="l">Contracts</div>
              <div className="v">{num(data && data.contracts)}</div>
            </div>
            <div
              className="stat"
              title="Distinct units that saw rental activity, over the active fleet"
            >
              <div className="l">Units Utilized</div>
              <div className="v">
                {num(data && data.units_rented)}
                <span style={{ color: "var(--muted)", fontSize: 12 }}>
                  {" "}
                  / {num(data && data.active_fleet)}
                </span>
              </div>
            </div>
            <div className="stat" title="Rental lines divided by distinct units rented">
              <div className="l">Turns / Unit</div>
              <div className="v">{num(data && data.turns_per_unit, 1)}</div>
            </div>
            <div className="stat" title="Mean StartDate to EndDate span">
              <div className="l">Avg Days</div>
              <div className="v">{num(data && data.avg_days, 1)}</div>
            </div>
            <div
              className="stat"
              title="Share of the active fleet that was rented at least once"
            >
              <div className="l">Fleet Utilized</div>
              <div className="v">{pct(data && data.fleet_on_rent_pct, 0)}</div>
            </div>
          </div>
          {data && data.top_units && data.top_units.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Stock No</th>
                  <th>Model</th>
                  <th className="num">Revenue</th>
                  <th className="num">Rentals</th>
                </tr>
              </thead>
              <tbody>
                {data.top_units.map((u, i) => (
                  <tr key={u.stock_no || i}>
                    <td>{u.stock_no}</td>
                    <td className="name">{u.model || u.description}</td>
                    <td className="num">{compactMoney(u.revenue)}</td>
                    <td className="num">{num(u.rentals)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </React.Fragment>
      </Body>
    </Panel>
  );
}

/* ------------------------------------------------------- invoice register */

/* README calls these 'common invoice types': `in` standard, `wo` work order,
 * `rl` rental. The column is two characters in the data, which means nothing to
 * a manager, so it is spelled out. */
const INVOICE_TYPE_LABELS = { in: "Invoice", wo: "Work order", rl: "Rental" };

const INVOICE_COLUMNS = [
  col.date("activity_date", "Date"),
  col.text("doc_no", "Doc #"),
  col.text("invoice_no", "Invoice #"),
  col.text("customer_no", "Cust #"),
  col.text("customer_name", "Customer", {
    render: (r) => <span className="name">{text(r.customer_name)}</span>,
  }),
  col.text("invoice_type", "Type", {
    render: (r) => text(INVOICE_TYPE_LABELS[r.invoice_type] || r.invoice_type),
  }),
  col.text("status", "Status", {
    hint: "Only the posted statuses count as revenue: finalized and archived",
  }),
  col.text("salesperson", "Salesperson"),
  col.num("lines", "Lines"),
  col.money("revenue", "Revenue", {
    hint: "Summed from the invoice's revenue lines, excluding trade-ins and quote lines",
  }),
  col.money("gross_profit", "GP (Units+Parts)", {
    hint: "Gross profit exists only for Units and Parts lines",
  }),
  col.pct("margin_pct", "GM %", {
    hint: "Gross profit over costed revenue, matching the KPI definition",
  }),
  col.money("trade_in_allowance", "Trade-In", {
    hint: "Value allowed against the deal; tracked separately, never netted off revenue",
  }),
  col.money("total_invoice", "Invoice Total", {
    hint: "InvoiceHeader.TotalInvoice, which also carries tax and misc charges",
  }),
];

/** The drill-down target for the Revenue and Invoices KPIs.
 *
 * Its row count and revenue tie exactly to those two cards, because it admits
 * exactly the invoices they count: posted status, and at least one line that is
 * neither a trade-in nor a quote.
 */
function InvoiceRegisterPanel({ params, filters, onFilters }) {
  return (
    <Panel
      title="Invoice Register"
      hint="every posted invoice behind the KPIs above"
    >
      <DataTable
        path="/api/invoices/detail"
        params={params}
        columns={INVOICE_COLUMNS}
        filters={filters}
        onFilters={onFilters}
        defaultSort="activity_date"
        searchHint="Search invoice #, doc #, customer name or #"
        note="Revenue is summed from the invoice's own revenue lines, so it will not match the invoice total, which also carries tax, miscellaneous charges, trade-ins and quote lines. Searching covers the whole register, not just the page on screen."
      />
    </Panel>
  );
}

/* ------------------------------------------------------------------- tab */

function OverviewTab({ params, grain, onGrain, onGoTab }) {
  // The trend hands the register a period so a bar click narrows it to that
  // month, quarter or year; it shows up as a removable chip on the table.
  const [invoiceFilters, setInvoiceFilters] = useState({});

  return (
    <React.Fragment>
      <OverviewKpis params={params} />

      <div className="grid g-trend">
        <TrendPanel
          params={params}
          grain={grain}
          onGrain={onGrain}
          onDrill={(f) => setInvoiceFilters((prev) => ({ ...prev, ...f }))}
        />
        <MixPanel params={params} />
      </div>

      <div className="grid g-3">
        <TopCustomersPanel
          params={params}
          onOpen={(r) => onGoTab("customers", { search: r.customer_name })}
        />
        <TopSalespeoplePanel
          params={params}
          onOpen={(name) => onGoTab("units", { salesperson: name })}
        />
        <div className="stack">
          <ServiceGaugePanel params={params} />
          <RentalSummaryPanel params={params} />
        </div>
      </div>

      <InvoiceRegisterPanel
        params={params}
        filters={invoiceFilters}
        onFilters={setInvoiceFilters}
      />
    </React.Fragment>
  );
}
