export function TickerTag({ symbol, exchange = 'NSE' }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 8px',
      background: 'rgba(255,255,255,0.06)',
      border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 4,
      fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 500,
      color: '#fff', letterSpacing: '0.04em',
    }}>
      <span style={{ color: 'rgba(255,255,255,0.35)', fontSize: 10 }}>{exchange}:</span>
      {symbol}
    </span>
  );
}
