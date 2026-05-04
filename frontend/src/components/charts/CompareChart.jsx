import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';

// Monochrome line palette — pure white primary, descending opacity for siblings.
const LINE_COLORS = [
  '#FFFFFF',
  'rgba(255,255,255,0.7)',
  'rgba(255,255,255,0.5)',
  'rgba(255,255,255,0.35)',
  'rgba(255,255,255,0.2)',
];

function buildChartRows(series) {
  const dateMap = new Map();
  for (const s of series || []) {
    for (const p of s.data || []) {
      if (!dateMap.has(p.date)) dateMap.set(p.date, { date: p.date });
      dateMap.get(p.date)[s.symbol] = p.value;
    }
  }
  return Array.from(dateMap.values()).sort((a, b) => a.date.localeCompare(b.date));
}

function formatTickDate(d) {
  if (!d) return '';
  const dt = new Date(d);
  if (Number.isNaN(dt.getTime())) return d;
  return dt.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' });
}

function formatValue(v, normalised) {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  if (normalised) return `${v.toFixed(2)}`;
  return `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function CustomTooltip({ active, payload, label, normalised }) {
  if (!active || !payload || payload.length === 0) return null;
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
      minWidth: 140,
    }}>
      <div style={{ color: 'rgba(255,255,255,0.4)', marginBottom: 6, letterSpacing: '0.04em' }}>
        {formatTickDate(label)}
      </div>
      {payload.map((entry) => (
        <div key={entry.dataKey} style={{
          display: 'flex', justifyContent: 'space-between', gap: 16, marginTop: 2,
        }}>
          <span style={{ color: '#fff' }}>{entry.dataKey}</span>
          <span style={{ color: 'rgba(255,255,255,0.7)' }}>
            {formatValue(entry.value, normalised)}
          </span>
        </div>
      ))}
    </div>
  );
}

function StatsCard({ s }) {
  const tr = s.stats?.total_return_pct ?? 0;
  const dd = s.stats?.max_drawdown_pct ?? 0;
  const vol = s.stats?.volatility_annualised ?? 0;
  const trColor = tr >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)';
  return (
    <div style={{
      flex: '0 0 auto',
      minWidth: 160,
      padding: '12px 14px',
      background: 'rgba(255,255,255,0.04)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 10,
      backdropFilter: 'blur(8px)',
      WebkitBackdropFilter: 'blur(8px)',
      fontFamily: 'var(--font-mono)',
    }}>
      <div style={{
        fontSize: 12, color: '#fff', letterSpacing: '0.04em', marginBottom: 8,
      }}>{s.display_name || s.symbol}</div>
      <Row label="Return" value={`${tr >= 0 ? '+' : ''}${tr.toFixed(2)}%`} valueColor={trColor} />
      <Row label="Max DD" value={`${dd.toFixed(2)}%`} valueColor="var(--color-loss, #ef4444)" />
      <Row label="Vol"    value={`${vol.toFixed(1)}%`} />
      {s.stats?.cagr_pct != null && (
        <Row label="CAGR" value={`${s.stats.cagr_pct.toFixed(2)}%`} />
      )}
      {s.stats?.total_invested != null && (
        <Row label="Invested" value={`₹${Math.round(s.stats.total_invested).toLocaleString('en-IN')}`} />
      )}
      {s.stats?.final_value != null && (
        <Row label="Value" value={`₹${Math.round(s.stats.final_value).toLocaleString('en-IN')}`} />
      )}
    </div>
  );
}

function Row({ label, value, valueColor = 'rgba(255,255,255,0.85)' }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, marginTop: 3 }}>
      <span style={{ color: 'rgba(255,255,255,0.35)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>{label}</span>
      <span style={{ color: valueColor }}>{value}</span>
    </div>
  );
}

function ChartCore({ data, height, normalised }) {
  const series = data?.series || [];
  const rows = buildChartRows(series);
  const everyNth = Math.max(1, Math.floor(rows.length / 6));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          tickFormatter={formatTickDate}
          axisLine={{ stroke: 'rgba(255,255,255,0.08)' }}
          tickLine={false}
          interval={everyNth - 1}
        />
        <YAxis
          tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          tickFormatter={(v) => normalised ? `${Math.round(v)}` : `₹${Math.round(v)}`}
          axisLine={{ stroke: 'rgba(255,255,255,0.08)' }}
          tickLine={false}
          domain={['auto', 'auto']}
          width={48}
        />
        <Tooltip content={<CustomTooltip normalised={normalised} />} cursor={{ stroke: 'rgba(255,255,255,0.15)' }} />
        {series.map((s, i) => (
          <Line
            key={s.symbol}
            type="monotone"
            dataKey={s.symbol}
            stroke={LINE_COLORS[i % LINE_COLORS.length]}
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 3, strokeWidth: 0, fill: LINE_COLORS[i % LINE_COLORS.length] }}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function EmptyOverlay({ message }) {
  return (
    <div style={{
      position: 'absolute', inset: 0, display: 'flex',
      alignItems: 'center', justifyContent: 'center',
      color: 'rgba(255,255,255,0.25)', fontSize: 12,
      fontFamily: 'var(--font-mono)', pointerEvents: 'none',
    }}>{message}</div>
  );
}

export function CompareChart({ data, height = 320, showStats = true }) {
  const series = data?.series || [];
  const normalised = data?.chart_type !== 'single' && (
    data?.chart_type === 'comparison' ||
    series.some((s) => (s.data?.[0]?.value ?? null) === 100)
  );
  const anyData = series.some((s) => s.data && s.data.length > 0);

  return (
    <div>
      <div style={{ position: 'relative' }}>
        <ChartCore data={data} height={height} normalised={normalised} />
        {!anyData && <EmptyOverlay message="No data to display" />}
      </div>
      {showStats && series.length > 0 && (
        <div style={{
          display: 'flex', gap: 10, marginTop: 16,
          overflowX: 'auto', paddingBottom: 4,
        }}>
          {series.map((s) => (
            <StatsCard key={s.symbol} s={s} />
          ))}
        </div>
      )}
      {series.some((s) => s.note) && (
        <div style={{
          marginTop: 10, fontSize: 11, fontFamily: 'var(--font-mono)',
          color: 'var(--color-loss, #ef4444)',
        }}>
          {series.filter((s) => s.note).map((s) => s.note).join(' · ')}
        </div>
      )}
    </div>
  );
}

export function CompareChartCompact({ data, height = 200 }) {
  const series = data?.series || [];
  const normalised = data?.chart_type !== 'single';
  const anyData = series.some((s) => s.data && s.data.length > 0);
  return (
    <div style={{ position: 'relative' }}>
      <ChartCore data={data} height={height} normalised={normalised} />
      {!anyData && <EmptyOverlay message="No data" />}
    </div>
  );
}
