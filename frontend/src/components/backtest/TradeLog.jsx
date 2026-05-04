import { useMemo, useState } from 'react';

const PAGE_SIZE = 20;

const COLUMNS = [
  { key: 'index',         label: '#',          sortable: false },
  { key: 'entry_date',    label: 'Date In',    sortable: true },
  { key: 'exit_date',     label: 'Date Out',   sortable: true },
  { key: 'holding_days',  label: 'Days',       sortable: true, align: 'right' },
  { key: 'entry_price',   label: 'Entry ₹',    sortable: true, align: 'right' },
  { key: 'exit_price',    label: 'Exit ₹',     sortable: true, align: 'right' },
  { key: 'net_pnl',       label: 'P&L ₹',      sortable: true, align: 'right' },
  { key: 'return_pct',    label: 'Return %',   sortable: true, align: 'right' },
  { key: 'exit_reason',   label: 'Exit Reason', sortable: true },
];

function formatINR(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return Math.round(v).toLocaleString('en-IN');
}

function formatPct(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
}

function exitReasonLabel(t) {
  if (t.skipped) return 'SKIPPED';
  if (!t.exit_date) return 'OPEN';
  return (t.exit_reason || '').replace(/_/g, ' ');
}

function downloadCsv(trades) {
  const head = ['#', 'Date In', 'Date Out', 'Days', 'Entry', 'Exit',
                'Quantity', 'P&L', 'Return %', 'Exit Reason'];
  const lines = [head.join(',')];
  trades.forEach((t, i) => {
    lines.push([
      i + 1,
      t.entry_date || '',
      t.exit_date || '',
      t.holding_days ?? '',
      t.entry_price ?? '',
      t.exit_price ?? '',
      t.quantity ?? '',
      t.net_pnl ?? '',
      t.return_pct ?? '',
      t.exit_reason || (t.skipped ? `skipped:${t.skip_reason || ''}` : 'open'),
    ].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(','));
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'backtest_trades.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function TradeLog({ trades = [] }) {
  const [sortKey, setSortKey] = useState('entry_date');
  const [sortDir, setSortDir] = useState('desc');
  const [page, setPage] = useState(0);

  const sorted = useMemo(() => {
    const arr = [...trades];
    arr.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      let cmp;
      if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
      else cmp = String(av).localeCompare(String(bv));
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return arr;
  }, [trades, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageRows = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const totalNet = trades
    .filter((t) => !t.skipped && t.exit_date && t.net_pnl != null)
    .reduce((s, t) => s + Number(t.net_pnl), 0);
  const closed = trades.filter((t) => !t.skipped && t.exit_date && t.net_pnl != null);
  const wins = closed.filter((t) => t.net_pnl > 0).length;
  const winRate = closed.length ? (wins / closed.length) * 100 : 0;

  const onSort = (key) => {
    if (key === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  return (
    <div style={{ fontFamily: 'var(--font-mono)' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '4px 4px 12px',
      }}>
        <div style={{
          fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase',
          color: 'rgba(255,255,255,0.3)', fontFamily: 'var(--font-ui)',
        }}>
          Trade Log · {trades.length} entries
        </div>
        <button onClick={() => downloadCsv(sorted)} style={{
          padding: '6px 12px',
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 8, color: 'rgba(255,255,255,0.7)',
          fontSize: 11, cursor: 'pointer', fontFamily: 'var(--font-mono)',
        }}>Download CSV</button>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
              {COLUMNS.map((c) => (
                <th key={c.key}
                    onClick={() => c.sortable && onSort(c.key)}
                    style={{
                      padding: '10px 12px',
                      textAlign: c.align || 'left',
                      fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase',
                      color: 'rgba(255,255,255,0.4)',
                      cursor: c.sortable ? 'pointer' : 'default',
                      userSelect: 'none',
                    }}>
                  {c.label}{c.sortable && sortKey === c.key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length} style={{
                  padding: '32px', textAlign: 'center',
                  color: 'rgba(255,255,255,0.25)',
                }}>No trades</td>
              </tr>
            )}
            {pageRows.map((t, i) => {
              const idx = page * PAGE_SIZE + i + 1;
              const isOpen = !t.skipped && !t.exit_date;
              const isSkipped = t.skipped;
              const pnl = t.net_pnl;
              const ret = t.return_pct;
              const retColor = (ret ?? 0) >= 0
                ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)';
              const rowStyle = isSkipped
                ? { opacity: 0.5, fontStyle: 'italic' }
                : {};
              return (
                <tr key={idx} style={{
                  borderBottom: '1px solid rgba(255,255,255,0.04)',
                  ...rowStyle,
                }}>
                  <td style={{ padding: '8px 12px', color: 'rgba(255,255,255,0.4)' }}>{idx}</td>
                  <td style={{ padding: '8px 12px', color: 'rgba(255,255,255,0.85)' }}>
                    {t.entry_date || '—'}
                  </td>
                  <td style={{ padding: '8px 12px', color: 'rgba(255,255,255,0.85)' }}>
                    {isOpen
                      ? <span style={{ padding: '2px 6px', borderRadius: 4,
                                        background: 'rgba(255,255,255,0.08)',
                                        color: '#fff', fontSize: 10 }}>OPEN</span>
                      : (t.exit_date || '—')}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right',
                                color: 'rgba(255,255,255,0.7)' }}>
                    {t.holding_days ?? '—'}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right',
                                color: 'rgba(255,255,255,0.85)' }}>
                    {t.entry_price != null ? Number(t.entry_price).toFixed(2) : '—'}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right',
                                color: 'rgba(255,255,255,0.85)' }}>
                    {t.exit_price != null ? Number(t.exit_price).toFixed(2) : '—'}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right',
                                color: pnl != null ? (pnl >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)') : 'rgba(255,255,255,0.5)' }}>
                    {pnl != null ? (pnl >= 0 ? '+' : '') + formatINR(pnl) : '—'}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', color: retColor }}>
                    {ret != null ? formatPct(ret) : '—'}
                  </td>
                  <td style={{ padding: '8px 12px', color: 'rgba(255,255,255,0.55)', fontSize: 11 }}>
                    {isSkipped ? (
                      <span style={{ padding: '2px 6px', borderRadius: 4,
                                      background: 'rgba(245,158,11,0.1)',
                                      color: '#f59e0b', fontSize: 10 }}>
                        SKIPPED
                      </span>
                    ) : exitReasonLabel(t)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '14px 4px 4px', fontSize: 11,
        color: 'rgba(255,255,255,0.5)',
      }}>
        <div>
          Showing {pageRows.length} of {sorted.length} trades · Net P&L:{' '}
          <span style={{ color: totalNet >= 0 ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)' }}>
            {totalNet >= 0 ? '+' : ''}₹{formatINR(totalNet)}
          </span>
          {' · '}Win rate: {winRate.toFixed(0)}%
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))} style={{
            padding: '4px 10px', background: 'transparent',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 6, color: page === 0 ? 'rgba(255,255,255,0.2)' : '#fff',
            cursor: page === 0 ? 'default' : 'pointer', fontSize: 11,
          }}>‹ Prev</button>
          <span style={{ fontSize: 11 }}>{page + 1} / {totalPages}</span>
          <button disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  style={{
                    padding: '4px 10px', background: 'transparent',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: 6,
                    color: page >= totalPages - 1 ? 'rgba(255,255,255,0.2)' : '#fff',
                    cursor: page >= totalPages - 1 ? 'default' : 'pointer', fontSize: 11,
                  }}>Next ›</button>
        </div>
      </div>
    </div>
  );
}
