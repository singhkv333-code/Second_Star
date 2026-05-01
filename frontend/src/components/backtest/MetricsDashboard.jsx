function fmtPct(v, signed = true, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const sign = signed && v >= 0 ? '+' : '';
  return `${sign}${Number(v).toFixed(digits)}%`;
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}

function fmtINR(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `₹${Math.round(v).toLocaleString('en-IN')}`;
}

const cardBase = {
  background: 'rgba(255,255,255,0.04)',
  border: '1px solid rgba(255,255,255,0.07)',
  borderRadius: 12,
  padding: '16px 20px',
  display: 'flex',
  flexDirection: 'column',
  gap: 6,
};

const labelStyle = {
  fontSize: 10,
  letterSpacing: '0.12em',
  textTransform: 'uppercase',
  color: 'rgba(255,255,255,0.3)',
  fontFamily: 'var(--font-ui)',
};

const subLabelStyle = {
  fontSize: 10,
  color: 'rgba(255,255,255,0.4)',
  fontFamily: 'var(--font-mono)',
  letterSpacing: '0.04em',
};

function MetricCard({ value, label, sub, color = '#fff', italic = false, mono = true }) {
  return (
    <div style={cardBase}>
      <div style={{
        fontFamily: italic ? 'var(--font-display)' : 'var(--font-mono)',
        fontStyle: italic ? 'italic' : 'normal',
        fontSize: italic ? 30 : 20,
        color, lineHeight: 1.1,
      }}>{value}</div>
      <div style={labelStyle}>{label}</div>
      {sub && <div style={subLabelStyle}>{sub}</div>}
    </div>
  );
}

function profitFactorColor(pf) {
  if (pf >= 1.5) return 'var(--color-profit, #22c55e)';
  if (pf >= 1.0) return '#f59e0b';
  return 'var(--color-loss, #ef4444)';
}

export function MetricsDashboard({ metrics = {}, strategyDefinition = {}, warnings = [] }) {
  const {
    total_return_pct = 0, benchmark_return_pct = 0,
    alpha_pct = 0, cagr_pct = 0,
    max_drawdown_pct = 0, sharpe_ratio = 0,
    annualised_volatility_pct = 0, profit_factor = 0,
    total_trades = 0, win_rate_pct = 0, avg_holding_days = 0,
    avg_winning_return_pct = 0, avg_losing_return_pct = 0,
    skipped_trades = 0,
    total_brokerage_paid = 0, total_stt_paid = 0,
    test_period_days = 0, start_date = '', end_date = '',
    outperformed_benchmark = false,
  } = metrics;

  const sym = strategyDefinition?.symbol || '—';
  const period = strategyDefinition?.period || '';
  const totalCosts = (total_brokerage_paid || 0) + (total_stt_paid || 0);

  const stratColor = total_return_pct >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)';
  const alphaColor = alpha_pct >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)';
  const cagrColor = cagr_pct >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {warnings && warnings.length > 0 && (
        <div style={{
          background: 'rgba(245,158,11,0.06)',
          border: '1px solid rgba(245,158,11,0.2)',
          borderRadius: 12, padding: '14px 18px',
          color: '#f59e0b', fontSize: 12, fontFamily: 'var(--font-mono)',
        }}>
          <div style={{ marginBottom: 6, letterSpacing: '0.06em', textTransform: 'uppercase', fontSize: 10 }}>
            ⚠ Warnings
          </div>
          {warnings.map((w, i) => (
            <div key={i} style={{ marginTop: 4, lineHeight: 1.5 }}>· {w}</div>
          ))}
        </div>
      )}

      {/* Section 1 — Returns */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <MetricCard
          value={fmtPct(total_return_pct)}
          color={stratColor} italic
          label="Strategy Return"
        />
        <MetricCard
          value={fmtPct(benchmark_return_pct)}
          color="rgba(255,255,255,0.7)" italic
          label="Nifty 50 (Buy & Hold)"
        />
        <MetricCard
          value={fmtPct(alpha_pct)}
          color={alphaColor} italic
          label="Alpha vs Nifty"
          sub={outperformed_benchmark ? 'Outperformed' : 'Underperformed'}
        />
        <MetricCard
          value={`${fmtPct(cagr_pct)} p.a.`}
          color={cagrColor} italic
          label="Annualised CAGR"
        />
      </div>

      {/* Section 2 — Risk */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
        <MetricCard
          value={fmtPct(max_drawdown_pct, true, 1)}
          color="var(--color-loss, #ef4444)"
          label="Max Drawdown"
        />
        <MetricCard
          value={fmtNum(sharpe_ratio)}
          color={sharpe_ratio >= 1 ? '#fff' : 'rgba(255,255,255,0.55)'}
          label="Sharpe Ratio"
        />
        <MetricCard
          value={`${fmtNum(annualised_volatility_pct, 1)}%`}
          color="#fff"
          label="Annual Volatility"
        />
        <MetricCard
          value={fmtNum(profit_factor)}
          color={profitFactorColor(profit_factor)}
          label="Profit Factor"
        />
      </div>

      {/* Section 3 — Trade stats (2 rows × 3 cols) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        <MetricCard value={total_trades} label="Total Trades" />
        <MetricCard value={`${fmtNum(win_rate_pct, 0)}%`} label="Win Rate" />
        <MetricCard value={fmtNum(avg_holding_days, 0)} label="Avg Hold Days" />
        <MetricCard
          value={fmtPct(avg_winning_return_pct, true, 1)}
          color="var(--color-profit, #22c55e)"
          label="Avg Win"
        />
        <MetricCard
          value={fmtPct(avg_losing_return_pct, true, 1)}
          color="var(--color-loss, #ef4444)"
          label="Avg Loss"
        />
        <MetricCard
          value={fmtINR(totalCosts)}
          label="Total Costs"
          sub={`Brokerage ${fmtINR(total_brokerage_paid)} · STT ${fmtINR(total_stt_paid)}`}
        />
      </div>

      {/* Section 4 — Period summary */}
      <div style={{
        ...cardBase,
        flexDirection: 'row', flexWrap: 'wrap', gap: 18,
        alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.7)', fontFamily: 'var(--font-mono)' }}>
          Tested <span style={{ color: '#fff' }}>{sym}</span> over{' '}
          <span style={{ color: '#fff' }}>{period.toUpperCase()}</span>
          {start_date && end_date && (
            <> ({start_date} to {end_date})</>
          )}
        </div>
        <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', fontFamily: 'var(--font-mono)' }}>
          {test_period_days} trading days · {total_trades} trades · {skipped_trades} skipped
        </div>
        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', fontFamily: 'var(--font-mono)' }}>
          Past performance does not guarantee future results.
        </div>
      </div>
    </div>
  );
}
