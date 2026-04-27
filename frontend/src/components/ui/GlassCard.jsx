import { forwardRef } from 'react';
import clsx from 'clsx';

export const GlassCard = forwardRef(({
  children, className, style, onClick, hoverable = true, padding = '20px', ...props
}, ref) => (
  <div
    ref={ref}
    onClick={onClick}
    className={clsx('glass', className)}
    style={{
      padding,
      cursor: onClick ? 'pointer' : 'default',
      ...style,
    }}
    {...props}
  >
    {children}
  </div>
));
GlassCard.displayName = 'GlassCard';

export function GlassSection({ label, children, style }) {
  return (
    <div style={{ marginBottom: 24, ...style }}>
      {label && (
        <div style={{
          fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase',
          color: 'rgba(255,255,255,0.3)', marginBottom: 12, fontFamily: 'var(--font-ui)',
        }}>{label}</div>
      )}
      {children}
    </div>
  );
}
