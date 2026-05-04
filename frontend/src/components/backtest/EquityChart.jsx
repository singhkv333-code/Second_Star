import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, AreaChart, Area,
} from 'recharts';

function formatINR(v) {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  return `₹${Math.round(v).toLocaleString('en-IN')}`;
}

function formatTickDate(d, periodYears) {
  if (!d) return '';
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return d;
  if (periodYears < 1) {
    return dt.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' });
  }
  if (periodYears <= 3) {
    const q = Math.floor(dt.getMonth() / 3) + 1;
    const yy = String(dt.getFullYear()).slice(-2);
    return `Q${q} '${yy}`;
  }
  return `'${String(dt.getFullYear()).slice(-2)}`;
}

function buildRows(equityCurve, benchmarkCurve) {
  const map = new Map();
  for (const p of equityCurve || []) {
    if (!map.has(p.date)) map.set(p.date, { date: p.date });
    map.get(p.date).strategy = p.value;
    map.get(p.date).drawdown_pct = p.drawdown_pct;
  }
  for (const p of benchmarkCurve || []) {
    if (!map.has(p.date)) map.set(p.date, { date: p.date });
    map.get(p.date).benchmark = p.value;
  }
  return Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
}

function CustomTooltip({ active, payload, label, startingCapital }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload || {};
  const strat = row.strategy;
  const bench = row.benchmark;
  const dd = row.drawdown_pct;
  const stratPct = strat != null && startingCapital
    ? ((strat - startingCapital) / startingCapital) * 100 : null;
  const benchPct = bench != null && startingCapital
    ? ((bench - startingCapital) / startingCapital) * 100 : null;
  return (
    <div style={{
      background: 'rgba(20,20,20,0.92)',
      border: '1px solid rgba(255,255,255,0.12)',
      backdropFilter: 'blur(12px)',
      WebkitBackdropFilter: 'blur(12px)',
      padding: '10px 14px',
      borderRadius: 10,
      fontFamily: 'var(--font-mono)',
      fontSize: 11,
      color: 'rgba(255,255,255,0.85)',
      minWidth: 180,
    }}>
      <div style={{ color: 'rgba(255,255,255,0.4)', marginBottom: 6 }}>
        {new Date(label).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}
      </div>
      {strat != null && (
        <Row label="Strategy"
             value={`${formatINR(strat)} (${stratPct >= 0 ? '+' : ''}${stratPct?.toFixed(1)}%)`}
             color={stratPct >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)'} />
      )}
      {bench != null && (
        <Row label="Nifty"
             value={`${formatINR(bench)} (${benchPct >= 0 ? '+' : ''}${benchPct?.toFixed(1)}%)`}
             color="rgba(255,255,255,0.7)" />
      )}
      {dd != null && (
        <Row label="Drawdown" value={`${dd.toFixed(1)}%`}
             color="var(--color-loss, #ef4444)" />
      )}
    </div>
  );
}

function Row({ label, value, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginTop: 2 }}>
      <span style={{ color: 'rgba(255,255,255,0.5)' }}>{label}</span>
      <span style={{ color }}>{value}</span>
    </div>
  );
}

export function EquityChart({
  equityCurve = [],
  benchmarkCurve = [],
  startingCapital = 500_000,
  metrics = {},
  height = 340,
  compact = false,
}) {
  const rows = buildRows(equityCurve, benchmarkCurve);
  if (rows.length === 0) {
    return (
      <div style={{
        height, display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'rgba(255,255,255,0.25)', fontSize: 12, fontFamily: 'var(--font-mono)',
      }}>No equity curve data</div>
    );
  }

  const periodYears = metrics?.test_period_years || 1;
  const interval = Math.max(1, Math.floor(rows.length / 6));

  const stratFinal = rows[rows.length - 1]?.strategy;
  const benchFinal = rows[rows.length - 1]?.benchmark;
  const stratReturn = metrics?.total_return_pct ?? 0;
  const benchReturn = metrics?.benchmark_return_pct ?? 0;
  const stratColor = stratReturn >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)';

  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            tickFormatter={(d) => formatTickDate(d, periodYears)}
            axisLine={{ stroke: 'rgba(255,255,255,0.08)' }}
            tickLine={false}
            interval={interval - 1}
          />
          <YAxis
            tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`}
            axisLine={{ stroke: 'rgba(255,255,255,0.08)' }}
            tickLine={false}
            width={56}
            domain={['auto', 'auto']}
          />
          <Tooltip
            content={<CustomTooltip startingCapital={startingCapital} />}
            cursor={{ stroke: 'rgba(255,255,255,0.15)' }}
          />
          <ReferenceLine
            y={startingCapital}
            stroke="rgba(255,255,255,0.15)"
            strokeDasharray="2 4"
            label={{ value: 'Start', fontSize: 10, fill: 'rgba(255,255,255,0.3)',
                     position: 'insideTopRight' }}
          />
          <Line type="monotone" dataKey="strategy" stroke="#FFFFFF" strokeWidth={1.5}
                 dot={false} isAnimationActive={false} connectNulls />
          <Line type="monotone" dataKey="benchmark" stroke="rgba(255,255,255,0.35)"
                 strokeWidth={1} strokeDasharray="4 2" dot={false}
                 isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>

      {!compact && (
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '12px 4px 4px',
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>
          <div style={{ display: 'flex', gap: 24 }}>
            <span style={{ color: '#fff', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: '#fff' }}>●</span> Strategy
              <span style={{ color: stratColor, marginLeft: 4 }}>
                {stratReturn >= 0 ? '+' : ''}{stratReturn.toFixed(1)}%
              </span>
            </span>
            <span style={{ color: 'rgba(255,255,255,0.7)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: 'rgba(255,255,255,0.45)' }}>- - -</span> Nifty 50
              <span style={{ color: 'rgba(255,255,255,0.7)', marginLeft: 4 }}>
                {benchReturn >= 0 ? '+' : ''}{benchReturn.toFixed(1)}%
              </span>
            </span>
          </div>
          <div style={{ color: 'rgba(255,255,255,0.4)' }}>
            {formatINR(stratFinal)} vs {formatINR(benchFinal)}
          </div>
        </div>
      )}

      {!compact && rows.some((r) => r.drawdown_pct != null) && (
        <div style={{ marginTop: 8 }}>
          <div style={{
            fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.3)', marginBottom: 8, fontFamily: 'var(--font-ui)',
          }}>Drawdown %</div>
          <ResponsiveContainer width="100%" height={120}>
            <AreaChart data={rows} margin={{ top: 4, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                tickFormatter={(d) => formatTickDate(d, periodYears)}
                axisLine={{ stroke: 'rgba(255,255,255,0.08)' }}
                tickLine={false}
                interval={interval - 1}
              />
              <YAxis
                tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                tickFormatter={(v) => `${v.toFixed(0)}%`}
                axisLine={{ stroke: 'rgba(255,255,255,0.08)' }}
                tickLine={false}
                width={56}
                reversed={false}
              />
              <Tooltip
                contentStyle={{
                  background: 'rgba(20,20,20,0.92)',
                  border: '1px solid rgba(255,255,255,0.12)',
                  borderRadius: 10, fontFamily: 'var(--font-mono)', fontSize: 11,
                }}
                formatter={(v) => `${Number(v).toFixed(2)}%`}
                labelFormatter={(d) => new Date(d).toLocaleDateString('en-IN')}
              />
              <Area type="monotone" dataKey="drawdown_pct"
                    stroke="rgba(239,68,68,0.5)" strokeWidth={1}
                    fill="rgba(239,68,68,0.15)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
