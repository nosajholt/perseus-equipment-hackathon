/* Service tab.
 *
 * Service has no cost basis anywhere in the schema - AppUser.HourlyRate is 0
 * for all 48 users - so nothing here is a margin. Every figure is revenue,
 * hours or a count, and the productivity measures are labelled as the proxies
 * they are.
 */

function ServiceKpis({ params }) {
  const { loading, data, error } = useEndpoint("/api/service/summary", params);
  const d = data || {};

  const cards = [
    { label: "Labor Revenue", value: compactMoney(d.revenue), note: "no cost basis" },
    { label: "Work Orders", value: num(d.work_orders), note: `${num(d.segments)} segments` },
    { label: "Actual Hours", value: num(d.actual_hrs), note: "billed on segments" },
    {
      label: "Effective Rate",
      value: money(d.effective_rate),
      note: `list rate ${money(d.avg_labor_rate)}`,
    },
    { label: "Revenue / WO", value: money(d.revenue_per_wo), note: `${num(d.hours_per_wo, 1)}h avg` },
    {
      label: "Billed vs Clocked",
      value: pct(d.billed_vs_clocked_pct),
      note: `${num(d.clock_hours)}h clocked by ${num(d.techs_clocked)} techs`,
    },
    {
      label: "Flat-Rate Share",
      value: pct(d.flat_rate_share_pct),
      note: "of segments, by BillAs",
    },
    {
      label: "Open Segments",
      value: num(d.wip_segments),
      note: `${num(d.wip_work_orders)} WOs, ${compactMoney(d.wip_value)}`,
      tone: d.wip_segments > 0 ? "warn" : null,
    },
  ];

  return <KpiGrid loading={loading} error={error} cards={cards} />;
}

function ServiceThroughputPanel({ data, loading, error, grain, onGrain }) {
  const rows = (data && data.throughput) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r.period);
    return {
      animationDuration: 350,
      grid: { top: 32, left: 48, right: 60, bottom: labels.length > 18 ? 44 : 30 },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.period === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Work orders <b>${num(r.work_orders)}</b><br/>Segments <b>${num(r.segments)}</b><br/>
            Actual hours <b>${num(r.actual_hrs)}</b><br/>Revenue <b>${compactMoney(
            r.revenue
          )}</b><br/>Effective rate <b>${money(r.effective_rate)}</b>`;
        },
      },
      xAxis: catAxis(labels),
      yAxis: [
        valAxis("Work orders"),
        { ...valAxis("Rate", (v) => money(v)), splitLine: { show: false } },
      ],
      series: [
        {
          name: "Work orders",
          type: "bar",
          barMaxWidth: 30,
          itemStyle: { color: PALETTE[2], borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => r.work_orders),
        },
        {
          name: "Effective rate / hr",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2, color: "#f7b32b" },
          data: rows.map((r) => (r.effective_rate == null ? null : +r.effective_rate.toFixed(2))),
        },
      ],
      dataZoom: labels.length > 24 ? [{ type: "inside" }] : undefined,
    };
  }, [data]);

  return (
    <Panel
      title="Work Order Throughput"
      right={<Segmented value={grain} options={GRAINS} onChange={onGrain} />}
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <EChart option={option} height={260} />
      </Body>
    </Panel>
  );
}

function ServiceTechPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.technicians) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    const ordered = rows.slice(0, 14).reverse();
    return {
      animationDuration: 350,
      grid: { top: 30, left: 8, right: 16, bottom: 4, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = ordered.find((x) => x.name === ps[0].name) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].name}</div>
            Clocked <b>${num(r.clock_hours)}h</b> over ${num(r.punches)} punches<br/>
            Of which clocked in <b>${num(r.clocked_in_hours)}h</b><br/>
            Segments touched <b>${num(r.segments)}</b><br/>
            Revenue on those segments <b>${compactMoney(r.segment_revenue)}</b><br/>
            <span style="color:#8b94a7">${money(r.revenue_per_hour)} per clocked hour</span>`;
        },
      },
      xAxis: valAxis(null, (v) => num(v)),
      yAxis: {
        type: "category",
        data: ordered.map((r) => r.name),
        axisLabel: { color: "#8b94a7", fontSize: 11 },
        axisLine: { lineStyle: { color: AXIS_LINE } },
        axisTick: { show: false },
      },
      series: [
        {
          name: "Clocked in",
          type: "bar",
          stack: "h",
          barMaxWidth: 16,
          itemStyle: { color: PALETTE[2] },
          data: ordered.map((r) => +(r.clocked_in_hours || 0).toFixed(1)),
        },
        {
          name: "Other logged time",
          type: "bar",
          stack: "h",
          barMaxWidth: 16,
          itemStyle: { color: "#33405a" },
          data: ordered.map((r) =>
            +Math.max((r.clock_hours || 0) - (r.clocked_in_hours || 0), 0).toFixed(1)
          ),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Technician Productivity" hint="clocked hours from WorkInProgress">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={300} onClick={(p) => onDrill({ tech: p.name })} />
          <Caveat>
            Hours are clocked time, not paid cost: HourlyRate is 0 for every user, so no
            labour cost or true efficiency can be computed. Revenue per clocked hour credits
            the whole segment to each technician who punched on it, so it over-counts on
            shared jobs.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function ServiceBillingPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.billing_mix) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r.bill_as);
    return {
      animationDuration: 350,
      grid: { top: 30, left: 52, right: 16, bottom: 26, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.bill_as === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Actual hours <b>${num(r.actual_hrs)}</b><br/>Flat-rate hours <b>${num(
            r.flat_rate_hrs
          )}</b><br/>Revenue <b>${compactMoney(r.revenue)}</b><br/>
            Effective rate <b>${money(r.effective_rate)}</b><br/>
            <span style="color:#8b94a7">${num(r.segments)} segments</span>`;
        },
      },
      xAxis: catAxis(labels, 0),
      yAxis: valAxis("Hours"),
      series: [
        {
          name: "Actual hours",
          type: "bar",
          barMaxWidth: 46,
          itemStyle: { color: PALETTE[0], borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => +(r.actual_hrs || 0).toFixed(1)),
        },
        {
          name: "Flat-rate hours",
          type: "bar",
          barMaxWidth: 46,
          itemStyle: { color: PALETTE[1], borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => +(r.flat_rate_hrs || 0).toFixed(1)),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Flat Rate vs Actual" hint="InvoiceSegment.BillAs">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={222} onClick={(p) => onDrill({ bill_as: p.name })} />
          <table>
            <thead>
              <tr>
                <th>Billed As</th>
                <th className="num">Segments</th>
                <th className="num">Revenue</th>
                <th className="num">Eff. Rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.bill_as}
                  className="rowlink"
                  onClick={() => onDrill({ bill_as: r.bill_as })}
                >
                  <td>{r.bill_as}</td>
                  <td className="num">{num(r.segments)}</td>
                  <td className="num">{compactMoney(r.revenue)}</td>
                  <td className="num">{money(r.effective_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Caveat>
            Flat-rate work records both a quoted and a worked figure, so the two bars differ
            there. StdJobRecommendedHrs is 0 on nearly every segment, so there is no standard
            time to compare either against.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function ServicePipelinePanel({ data, loading, error, onDrill }) {
  const rows = (data && data.pipeline) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 12),
      labelKey: "wo_status",
      valueKey: "work_orders",
      format: (v) => num(v),
      color: PALETTE[5],
      tooltip: (r) =>
        `Work orders <b>${num(r.work_orders)}</b><br/>Segments <b>${num(
          r.segments
        )}</b><br/>Revenue <b>${compactMoney(r.revenue)}</b><br/>Actual hours <b>${num(
          r.actual_hrs
        )}</b>`,
    });
  }, [data]);

  return (
    <Panel title="Work Order Status Pipeline" hint="status at time of billing">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={280} onClick={(p) => onDrill({ wo_status: p.name })} />
          <Caveat>
            WOStatusId is the status the work order was left in, and these are all already
            booked, so this shows where finished jobs stop rather than a live queue. Use the
            WIP panel for outstanding work.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function ServiceWipPanel({ data, loading, error, asOf, onDrill }) {
  const rows = (data && data.wip_aging) || [];
  const totals = rows.reduce(
    (a, r) => ({
      wos: a.wos + r.work_orders,
      value: a.value + r.value,
      booked: a.booked + r.on_booked_docs,
    }),
    { wos: 0, value: 0, booked: 0 }
  );

  const option = useMemo(() => {
    if (!rows.length) return null;
    return {
      animationDuration: 350,
      grid: { top: 30, left: 12, right: 56, bottom: 34, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.bucket === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Work orders <b>${num(r.work_orders)}</b><br/>Open segments <b>${num(
            r.segments
          )}</b><br/>Open value <b>${compactMoney(r.value)}</b><br/>
            <span style="color:#8b94a7">${num(r.on_booked_docs)} sit on already-booked invoices</span>`;
        },
      },
      xAxis: catAxis(rows.map((r) => r.bucket), 20),
      yAxis: [
        valAxis("Work orders"),
        { ...valAxis("Value", (v) => compactMoney(v)), splitLine: { show: false } },
      ],
      series: [
        {
          name: "Work orders",
          type: "bar",
          barMaxWidth: 40,
          data: rows.map((r, i) => ({
            value: r.work_orders,
            itemStyle: { color: RAMP[Math.min(i + 1, RAMP.length - 1)], borderRadius: [3, 3, 0, 0] },
          })),
        },
        {
          name: "Open value",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: "#e6e9ef" },
          itemStyle: { color: "#e6e9ef" },
          data: rows.map((r) => +(r.value || 0).toFixed(2)),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="WIP Aging" hint={`open segments as of ${asOf || "-"}`}>
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={252} onClick={(p) => onDrill({ bucket: p.name })} />
          <Caveat>
            {num(totals.wos)} work orders still carry a segment marked 'open', worth{" "}
            {compactMoney(totals.value)}. {num(totals.booked)} of them sit on invoices that
            were already finalized or archived, so most of this is a housekeeping backlog
            rather than unbilled work; the doc status column in the table separates the two.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

/* ------------------------------------------------------------------- tab */

const SERVICE_SEGMENT_COLUMNS = [
  col.date("activity_date", "Date"),
  col.text("doc_no", "WO"),
  col.text("customer_name", "Customer", {
    render: (r) => <span className="name">{text(r.customer_name)}</span>,
  }),
  col.text("tech", "Tech"),
  col.text("wo_status", "WO Status"),
  col.text("bill_as", "Billed As"),
  col.text("segment_status", "Segment"),
  col.text("unit_model", "Unit"),
  col.hours("actual_hrs", "Actual"),
  col.hours("flat_rate_hrs", "Flat Rate"),
  col.exact("labor_rate", "Rate"),
  col.exact("shop_fee", "Shop Fee"),
  col.num("meter", "Meter"),
  col.exact("revenue", "Revenue"),
  col.exact("effective_rate", "Eff. Rate"),
];

const SERVICE_WIP_COLUMNS = [
  col.date("activity_date", "Opened"),
  col.text("doc_no", "WO"),
  col.text("doc_status", "Doc Status", {
    hint: "Whether the invoice itself is still open",
  }),
  col.text("wo_status", "WO Status"),
  col.text("tech", "Tech"),
  col.text("customer_name", "Customer", {
    render: (r) => <span className="name">{text(r.customer_name)}</span>,
  }),
  col.text("unit_model", "Unit"),
  col.num("open_segments", "Open Segs"),
  col.hours("actual_hrs", "Hours"),
  col.exact("open_value", "Open Value"),
  col.num("age_days", "Age (days)"),
];

function ServiceTab({ params, grain, onGrain, initialFilters }) {
  const charts = useEndpoint("/api/service/charts", params ? { ...params, grain } : null);
  const summary = useEndpoint("/api/service/summary", params);
  const [segFilters, setSegFilters] = useState(initialFilters || {});
  const [wipFilters, setWipFilters] = useState({});

  useEffect(() => {
    if (initialFilters) setSegFilters(initialFilters);
  }, [JSON.stringify(initialFilters || {})]);

  const asOf = summary.data && summary.data.as_of;
  const shared = { data: charts.data, loading: charts.loading, error: charts.error };

  return (
    <React.Fragment>
      <ServiceKpis params={params} />

      <div className="grid g-2">
        <ServiceThroughputPanel {...shared} grain={grain} onGrain={onGrain} />
        <ServiceTechPanel {...shared} onDrill={setSegFilters} />
      </div>

      <div className="grid g-3">
        <ServiceBillingPanel {...shared} onDrill={setSegFilters} />
        <ServiceWipPanel {...shared} asOf={asOf} onDrill={setWipFilters} />
        <ServicePipelinePanel {...shared} onDrill={setSegFilters} />
      </div>

      <Panel title="Labor Segments" hint="every booked SL line in range">
        <DataTable
          path="/api/service/detail"
          params={params}
          columns={SERVICE_SEGMENT_COLUMNS}
          filters={segFilters}
          onFilters={setSegFilters}
          defaultSort="activity_date"
          searchHint="Search WO, customer, description, unit"
          note="Tech is InvoiceHeader.WOTechId, the assigned technician, set on 11,263 of 15,160 booked work orders; unassigned rows show as Tech <id> or Unassigned."
        />
      </Panel>

      <Panel title="Work In Progress" hint={`open segments as of ${asOf || "-"}`}>
        <DataTable
          path="/api/service/wip"
          params={params}
          columns={SERVICE_WIP_COLUMNS}
          filters={wipFilters}
          onFilters={setWipFilters}
          defaultSort="age_days"
          searchHint="Search WO, customer, unit"
          note="This snapshot ignores the date filter's start, since an old work order left open is exactly what matters; age runs to the end of the selected window."
        />
      </Panel>
    </React.Fragment>
  );
}
