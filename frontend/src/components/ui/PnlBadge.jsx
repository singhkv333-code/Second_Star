export function PnlBadge({ value, showSign = true, size = 'md' }) {
  const isProfit = value >= 0;
  const sign = showSign ? (isProfit ? '+' : '') : '';
  const color = isProfit ? 'var(--color-profit)' : 'var(--color-loss)';
  const fontSize = size === 'sm' ? 12 : size === 'lg' ? 20 : 14;

  return (
    <span style={{
      color, fontFamily: 'var(--font-mono)',
      fontSize, fontWeight: 500, letterSpacing: '-0.02em',
    }}>
      {sign}₹{Math.abs(value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
    </span>
  );
}

export function PnlPct({ value, size = 'md' }) {
  const isProfit = value >= 0;
  const color = isProfit ? 'var(--color-profit)' : 'var(--color-loss)';
  const fontSize = size === 'sm' ? 11 : size === 'lg' ? 16 : 13;

  return (
    <span style={{
      color, fontFamily: 'var(--font-mono)', fontSize,
      opacity: 0.8,
    }}>
      {isProfit ? '+' : ''}{value.toFixed(2)}%
    </span>
  );
}
