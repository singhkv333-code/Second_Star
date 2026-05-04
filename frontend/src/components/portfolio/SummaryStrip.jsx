import { usePortfolioStore } from '../../store/portfolioStore';
import { PnlBadge, PnlPct } from '../ui/PnlBadge';
import { LiveDot } from '../ui/LiveDot';

export function SummaryStrip() {
  const { summary, lastUpdated } = usePortfolioStore();
  const isMarketOpen = (() => {
    const now = new Date();
    const h = now.getHours(), m = now.getMinutes();
    const t = h * 60 + m;
    const day = now.getDay();
    return day >= 1 && day <= 5 && t >= 555 && t <= 930;
  })();

  if (!summary) return (
    <div style={{ padding: '16px 24px', display: 'flex', gap: 32 }}>
      {[1,2,3,4].map((i) => (
        <div key={i} style={{
          height: 36, width: 120, borderRadius: 6,
          background: 'rgba(255,255,255,0.04)',
          animation: 'pulse 1.5s ease-in-out infinite',
        }} />
      ))}
    </div>
  );

  const metrics = [
    { label: 'Portfolio Value', value: `₹${summary.total_value?.toLocaleString('en-IN', {maximumFractionDigits:0})}`, large: true },
    { label: 'Day P&L', custom: <PnlBadge value={summary.day_pnl || 0} size="lg" /> },
    { label: 'Total P&L', custom: (
      <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <PnlBadge value={summary.total_pnl || 0} />
        <PnlPct value={summary.total_pnl_pct || 0} size="sm" />
      </span>
    )},
    { label: 'Holdings', value: summary.num_holdings },
  ];

  return (
    <div style={{
      display: 'flex', alignItems: 'center',
      padding: '0 24px', gap: 0,
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      background: 'rgba(255,255,255,0.02)',
    }}>
      {metrics.map((m, i) => (
        <div key={i} style={{
          padding: '14px 24px', flex: i === 0 ? 2 : 1,
          borderRight: i < metrics.length - 1 ? '1px solid rgba(255,255,255,0.06)' : 'none',
        }}>
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 6 }}>
            {m.label}
          </div>
          {m.custom || (
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: m.large ? 22 : 16,
              color: '#fff', letterSpacing: '-0.02em',
            }}>{m.value}</div>
          )}
        </div>
      ))}

      <div style={{ padding: '14px 24px', marginLeft: 'auto' }}>
        <LiveDot active={isMarketOpen} />
        {lastUpdated && (
          <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', marginTop: 4 }}>
            {new Date(lastUpdated).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
          </div>
        )}
      </div>
    </div>
  );
}
