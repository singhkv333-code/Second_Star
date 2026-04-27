export function LiveDot({ active = true }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%',
        background: active ? 'var(--color-profit)' : 'rgba(255,255,255,0.2)',
        animation: active ? 'pulse 2s ease-in-out infinite' : 'none',
        display: 'inline-block',
      }} />
      <span style={{
        fontSize: 11, color: active ? 'var(--color-profit)' : 'rgba(255,255,255,0.3)',
        letterSpacing: '0.06em', textTransform: 'uppercase',
      }}>
        {active ? 'Live' : 'Closed'}
      </span>
    </span>
  );
}
