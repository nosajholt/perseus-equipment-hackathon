/* Customers tab.
 *
 * Gross profit here only ever covers Units and Parts, the two departments with
 * a cost basis, while revenue covers all of them - so no margin percentage is
 * shown against a customer. The receivable balance is a netting proxy, not an
 * aged trial balance, and is labelled as such everywhere it appears.
 */

const SEGMENT_COLORS = {
  New: "#4ec9a5",
  Active: "#4c9be8",
  "At risk": "#f7b32b",
  Churned: "#e8615d",
  "Never purchased": "#6b7488",
};

function CustomersKpis({ params }) {
  const { loading, data, error } = useEndpoint("/api/customers/summary", params);
  const d = data || {};

  const cards = [
    {
      label: "Active Accounts",
      value: num(d.active),
      note: `of ${num(d.customers_on_file)} on file`,
    },
    { label: "Revenue", value: compactMoney(d.revenue), note: "all departments" },
    {
      label: "Revenue / Account",
      value: compactMoney(d.revenue_per_customer),
      note: `${compactMoney(d.gross_profit)} GP on Units+Parts`,
    },
    {
      label: "Top 10 Share",
      value: pct(d.top10_share_pct),
      note: `${compactMoney(d.top10_revenue)} concentrated`,
      tone: d.top10_share_pct > 50 ? "warn" : null,
    },
    { label: "New Accounts", value: num(d.new_customers), note: "first purchase in range" },
    {
      label: "At Risk",
      value: num(d.at_risk),
      note: `${num(d.churned)} churned over a year`,
      tone: d.at_risk > 0 ? "warn" : null,
    },
    {
      label: "Credit Extended",
      value: compactMoney(d.credit_limit),
      note: `${num(d.on_hold)} accounts on hold`,
    },
    {
      label: "AR Balance",
      value: compactMoney(d.ar_balance),
      note: `proxy; ${num(d.over_limit)} over their limit`,
    },
  ];

  return <KpiGrid loading={loading} error={error} cards={cards} />;
}

function CustomersRetentionPanel({ data, loading, error }) {
  const rows = (data && data.retention) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    const labels = rows.map((r) => r.year);
    return {
      animationDuration: 350,
      grid: { top: 32, left: 48, right: 56, bottom: 30 },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.year === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Active <b>${num(r.active)}</b> (${num(r.new)} new, ${num(r.returning)} returning)<br/>
            Retained from prior year <b>${pct(r.retained_pct)}</b><br/>
            Lost from prior year <b>${num(r.churned)}</b><br/>
            Revenue <b>${compactMoney(r.revenue)}</b>`;
        },
      },
      xAxis: catAxis(labels, 0),
      yAxis: [
        valAxis("Accounts"),
        {
          ...valAxis("Retention", (v) => v + "%"),
          max: 100,
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "Returning",
          type: "bar",
          stack: "a",
          barMaxWidth: 34,
          itemStyle: { color: PALETTE[0] },
          data: rows.map((r) => r.returning),
        },
        {
          name: "New",
          type: "bar",
          stack: "a",
          barMaxWidth: 34,
          itemStyle: { color: PALETTE[2], borderRadius: [3, 3, 0, 0] },
          data: rows.map((r) => r.new),
        },
        {
          name: "Retained %",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: "#f7b32b" },
          itemStyle: { color: "#f7b32b" },
          connectNulls: true,
          data: rows.map((r) => (r.retained_pct == null ? null : +r.retained_pct.toFixed(1))),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Retention & Churn by Year" hint="cohorts are annual regardless of grain">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={262} />
          <Caveat>
            'New' means the account's first ever purchase, checked against all history
            rather than the selected window, so narrowing the dates does not turn old
            accounts into new ones. The first year shown has no prior year to retain
            from, so its retention is blank.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function CustomersTopPanel({ data, loading, error, onDrill }) {
  const [view, setView] = useState("top");
  const rows = (data && (view === "top" ? data.top : data.at_risk)) || [];
  const isTop = view === "top";
  const valueKey = isTop ? "revenue_in_window" : "revenue_all_time";

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 12),
      labelKey: "customer_name",
      valueKey,
      color: (r) => SEGMENT_COLORS[r.segment] || PALETTE[0],
      tooltip: (r) =>
        `<span style="color:#8b94a7">${text(r.city)}, ${text(r.state)} - ${text(
          r.class
        )}</span><br/>
         ${isTop ? "Revenue in range" : "Lifetime revenue"} <b>${compactMoney(
          r[valueKey]
        )}</b><br/>
         ${
           isTop
             ? `GP (Units+Parts) <b>${compactMoney(r.gross_profit_in_window)}</b><br/>
                Invoices <b>${num(r.invoices_in_window)}</b><br/>`
             : `Invoices all time <b>${num(r.invoices_all_time)}</b><br/>`
         }
         Last purchase <b>${text(r.last_purchase, "never")}</b> (${num(
          r.recency_days
        )} days)<br/>
         Segment <b>${text(r.segment)}</b>, AR <b>${compactMoney(r.ar_balance)}</b>`,
    });
  }, [data, view]);

  return (
    <Panel
      title="Accounts"
      right={
        <Segmented
          value={view}
          options={[
            { key: "top", label: "Top in range" },
            { key: "at_risk", label: "Lapsed" },
          ]}
          onChange={setView}
        />
      }
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={288}
            onClick={(p) => onDrill({ search: p.name })}
          />
          <Caveat>
            {isTop
              ? "Bar colour is the account's segment. Gross profit covers Units and Parts only, so it is not comparable to the revenue bar as a margin."
              : "Lapsed accounts are ranked by lifetime revenue: these bought nothing inside the selected window but have real history behind them."}
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function CustomersSegmentPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.segments) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return donutOption({
      rows,
      labelKey: "segment",
      valueKey: "customers",
      caption: "accounts",
      format: (v) => num(v),
      colors: rows.map((r) => SEGMENT_COLORS[r.segment] || PALETTE[0]),
    });
  }, [data]);

  return (
    <Panel title="Account Segments" hint="relative to the selected window">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={218} onClick={(p) => onDrill({ segment: p.name })} />
          <table>
            <thead>
              <tr>
                <th>Segment</th>
                <th className="num">Accounts</th>
                <th className="num">Revenue</th>
                <th className="num">AR</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.segment}
                  className="rowlink"
                  onClick={() => onDrill({ segment: r.segment })}
                >
                  <td>
                    <span
                      className="sw"
                      style={{ background: SEGMENT_COLORS[r.segment] || PALETTE[0] }}
                    />
                    {r.segment}
                  </td>
                  <td className="num">{num(r.customers)}</td>
                  <td className="num">{compactMoney(r.revenue)}</td>
                  <td className="num">{compactMoney(r.ar_balance)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Caveat>
            Recency is measured from the end of the date filter, not from today, so
            moving the window re-classifies every account against that date. At risk is
            up to a year since the last purchase; churned is beyond that.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function CustomersCreditPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.credit) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return {
      animationDuration: 350,
      grid: { top: 30, left: 12, right: 58, bottom: 30, containLabel: true },
      legend: legendStyle,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        ...TOOLTIP_STYLE,
        formatter: (ps) => {
          const r = rows.find((x) => x.credit_band === ps[0].axisValue) || {};
          return `<div style="font-weight:650;margin-bottom:4px">${ps[0].axisValue}</div>
            Accounts <b>${num(r.customers)}</b> (${num(r.active)} active in range)<br/>
            Credit extended <b>${compactMoney(r.credit_limit)}</b><br/>
            AR balance <b>${compactMoney(r.ar_balance)}</b><br/>
            Revenue in range <b>${compactMoney(r.revenue)}</b>`;
        },
      },
      xAxis: catAxis(rows.map((r) => r.credit_band), 20),
      yAxis: [
        valAxis("Accounts"),
        { ...valAxis("Exposure", (v) => compactMoney(v)), splitLine: { show: false } },
      ],
      series: [
        {
          name: "Accounts",
          type: "bar",
          barMaxWidth: 36,
          data: rows.map((r, i) => ({
            value: r.customers,
            itemStyle: { color: RAMP[Math.min(i, RAMP.length - 1)], borderRadius: [3, 3, 0, 0] },
          })),
        },
        {
          name: "AR balance",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { width: 2, color: "#e6e9ef" },
          itemStyle: { color: "#e6e9ef" },
          data: rows.map((r) => +(r.ar_balance || 0).toFixed(2)),
        },
      ],
    };
  }, [data]);

  return (
    <Panel title="Credit Exposure" hint="Customer.CredLimit bands">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={222} onClick={(p) => onDrill({ band: p.name })} />
          <Caveat>
            The AR figure nets the 'recv' rows written when an invoice goes on account
            against the 'recvpmt' rows written when it is settled. There is no AR ledger
            or ageing in this schema, so treat it as an outstanding-balance proxy.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function CustomersGeoPanel({ data, loading, error, onDrill }) {
  const [level, setLevel] = useState("state");
  const rows = (data && (level === "state" ? data.states : data.cities)) || [];
  const labelKey = level === "state" ? "state" : "city";

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 12),
      labelKey,
      valueKey: "revenue",
      color: PALETTE[0],
      tooltip: (r) =>
        `Revenue <b>${compactMoney(r.revenue)}</b><br/>Customers <b>${num(
          r.customers
        )}</b>${r.invoices ? `<br/>Invoices <b>${num(r.invoices)}</b>` : ""}`,
    });
  }, [data, level]);

  return (
    <Panel
      title="Revenue by Geography"
      right={
        <Segmented
          value={level}
          options={[
            { key: "state", label: "State" },
            { key: "city", label: "City" },
          ]}
          onChange={setLevel}
        />
      }
    >
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={252}
            onClick={(p) =>
              onDrill(level === "state" ? { state: p.name } : { search: p.name.split(",")[0] })
            }
          />
          <Caveat>
            Geography is the invoice's own billing address, not the customer record:
            CustomerAddress.IsDefault is set on only 556 of 6,838 rows. The detail table
            filters on the customer's address instead, so the two can disagree for
            accounts that moved or bill elsewhere.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function CustomersPaymentPanel({ data, loading, error }) {
  const rows = (data && data.payments) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return donutOption({
      rows: rows.slice(0, 8),
      labelKey: "method",
      valueKey: "amount",
      caption: "payments",
      colors: PALETTE,
    });
  }, [data]);

  return (
    <Panel title="Payment Mix" hint="Payment rows on booked invoices">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart option={option} height={214} />
          <table>
            <thead>
              <tr>
                <th>Method</th>
                <th>Type</th>
                <th className="num">Count</th>
                <th className="num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 7).map((r) => (
                <tr key={r.method + r.pay_type}>
                  <td className="name">{text(r.method)}</td>
                  <td>{text(r.pay_type)}</td>
                  <td className="num">{num(r.payments)}</td>
                  <td className="num">{compactMoney(r.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Caveat>
            Payment rows include both the receivable raised and its later settlement, so
            the total here is gross movement across the account and will exceed invoiced
            revenue rather than matching it.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

function CustomersClassPanel({ data, loading, error, onDrill }) {
  const rows = (data && data.classes) || [];

  const option = useMemo(() => {
    if (!rows.length) return null;
    return rankedBarOption({
      rows: rows.slice(0, 10),
      labelKey: "class",
      valueKey: "revenue",
      color: PALETTE[6],
      tooltip: (r) =>
        `Revenue <b>${compactMoney(r.revenue)}</b><br/>Accounts <b>${num(
          r.customers
        )}</b> (${num(r.active)} active)<br/>GP on Units+Parts <b>${compactMoney(
          r.gross_profit
        )}</b><br/>Credit extended <b>${compactMoney(r.credit_limit)}</b>`,
    });
  }, [data]);

  return (
    <Panel title="Revenue by Customer Class" hint="CustomerClassType">
      <Body loading={loading} error={error} empty={!rows.length}>
        <React.Fragment>
          <EChart
            option={option}
            height={252}
            onClick={(p) => onDrill({ customer_class: p.name })}
          />
          <Caveat>
            An account can carry several classes; the lowest-numbered one is used so a
            customer is counted once. IsBusiness is set on only 70 accounts and is
            ignored here in favour of this classification.
          </Caveat>
        </React.Fragment>
      </Body>
    </Panel>
  );
}

/* ------------------------------------------------------------------- tab */

const CUSTOMER_COLUMNS = [
  col.text("customer_no", "Cust #"),
  col.text("customer_name", "Customer", {
    render: (r) => <span className="name">{text(r.customer_name)}</span>,
  }),
  col.text("city", "City"),
  col.text("state", "St"),
  col.text("class", "Class"),
  col.text("segment", "Segment", {
    render: (r) => (
      <span className="tag" style={{ color: SEGMENT_COLORS[r.segment] || "var(--muted)" }}>
        {text(r.segment)}
      </span>
    ),
  }),
  col.text("salesperson", "Salesperson"),
  col.money("revenue_in_window", "Revenue (range)"),
  col.money("gross_profit_in_window", "GP (Units+Parts)", {
    hint: "Gross profit exists only for Units and Parts lines",
  }),
  col.num("invoices_in_window", "Invoices"),
  col.money("revenue_all_time", "Revenue (all)"),
  col.date("first_purchase", "First"),
  col.date("last_purchase", "Last"),
  col.num("recency_days", "Days Since"),
  col.money("credit_limit", "Credit Limit"),
  col.money("ar_balance", "AR (proxy)"),
];

function CustomersTab({ params, initialFilters }) {
  const charts = useEndpoint("/api/customers/charts", params);
  const [filters, setFilters] = useState(initialFilters || {});

  useEffect(() => {
    if (initialFilters) setFilters(initialFilters);
  }, [JSON.stringify(initialFilters || {})]);

  const shared = { data: charts.data, loading: charts.loading, error: charts.error };

  return (
    <React.Fragment>
      <CustomersKpis params={params} />

      <div className="grid g-2">
        <CustomersRetentionPanel {...shared} />
        <CustomersTopPanel {...shared} onDrill={setFilters} />
      </div>

      <div className="grid g-3">
        <CustomersSegmentPanel {...shared} onDrill={setFilters} />
        <CustomersCreditPanel {...shared} onDrill={setFilters} />
        <CustomersGeoPanel {...shared} onDrill={setFilters} />
      </div>

      <div className="grid g-2">
        <CustomersClassPanel {...shared} onDrill={setFilters} />
        <CustomersPaymentPanel {...shared} />
      </div>

      <Panel title="Customer Accounts" hint="every account on file">
        <DataTable
          path="/api/customers/detail"
          params={params}
          columns={CUSTOMER_COLUMNS}
          filters={filters}
          onFilters={setFilters}
          defaultSort="revenue_in_window"
          searchHint="Search name, customer #, city"
          note="Accounts with no activity in range are kept so lapsed and never-purchased ones stay visible; sort by revenue in range to push them to the bottom."
        />
      </Panel>
    </React.Fragment>
  );
}
