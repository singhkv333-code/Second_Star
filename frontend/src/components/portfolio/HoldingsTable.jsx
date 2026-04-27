import { useState } from 'react';
import { usePortfolioStore } from '../../store/portfolioStore';
import { PnlBadge, PnlPct } from '../ui/PnlBadge';
import { TickerTag } from '../ui/TickerTag';

export function HoldingsTable() {
  const { holdings } = usePortfolioStore();
  const [sortKey, setSortKey] = useState('pnl');
  const [sortDir, setSortDir] = useState(-1);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => -d);
    else { setSortKey(key); setSortDir(-1); }
  };

  const sorted = [...holdings].sort((a, b) => {
    const av = a[sortKey] ?? 0, bv = b[sortKey] ?? 0;
    return (av - bv) * sortDir;
  });

  const colStyle = (key) => ({
    padding: '10px 12px', fontSize: 11,
    color: sortKey === key ? '#fff' : 'rgba(255,255,255,0.3)',
    letterSpacing: '0.08em', textTransform: 'uppercase',
    cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap',
    transition: 'color 150ms',
  });

  if (!holdings.length) return (
    <div style={{ padding: '40px', textAlign: 'center', color: 'rgba(255,255,255,0.2)', fontSize: 13 }}>
      No holdings data. Connect your Zerodha account.
    </div>
  );

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            {[
              ['Symbol', 'tradingsymbol'],
              ['Qty', 'quantity'],
              ['Avg Price', 'average_price'],
              ['LTP', 'last_price'],
              ['P&L', 'pnl'],
              ['Day Chg', 'day_change_percentage'],
              ['Value', null],
            ].map(([label, key]) => (
              <th key={label}
                style={colStyle(key)}
                onClick={() => key && toggleSort(key)}
              >
                {label} {sortKey === key ? (sortDir > 0 ? '↑' : '↓') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="stagger">
          {sorted.map((h, i) => {
            const value = (h.last_price * h.quantity);
            return (
              <tr key={h.tradingsymbol || i} style={{
                borderBottom: '1px solid rgba(255,255,255,0.04)',
                transition: 'background 150ms',
              }}
              onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.03)'}
              onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
              >
                <td style={{ padding: '12px 12px' }}>
                  <TickerTag symbol={h.tradingsymbol} exchange={h.exchange || 'NSE'} />
                  {h.sector && (
                    <span style={{ display: 'block', fontSize: 10, color: 'rgba(255,255,255,0.25)', marginTop: 3 }}>
                      {h.sector}
                    </span>
                  )}
                </td>
                {[
                  [h.quantity, false],
                  [`₹${h.average_price?.toLocaleString('en-IN', {maximumFractionDigits:2})}`, true],
                  [`₹${h.last_price?.toLocaleString('en-IN', {maximumFractionDigits:2})}`, true],
                ].map(([val, mono], j) => (
                  <td key={j} style={{ padding: '12px 12px', fontFamily: mono ? 'var(--font-mono)' : 'var(--font-ui)', fontSize: 13, color: '#fff' }}>
                    {val}
                  </td>
                ))}
                <td style={{ padding: '12px 12px' }}>
                  <PnlBadge value={h.pnl || 0} size="sm" />
                </td>
                <td style={{ padding: '12px 12px' }}>
                  <PnlPct value={h.day_change_percentage || 0} size="sm" />
                </td>
                <td style={{ padding: '12px 12px', fontFamily: 'var(--font-mono)', fontSize: 13, color: 'rgba(255,255,255,0.7)' }}>
                  ₹{value.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
