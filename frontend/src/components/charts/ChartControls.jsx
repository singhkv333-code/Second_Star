import { useState } from 'react';

const PERIODS = ['1w', '1m', '3m', '6m', '1y', '2y', '5y', 'ytd', 'max'];
const TICKER_RE = /^[A-Z][A-Z0-9&-]{1,9}$/;

function Chip({ children, onRemove }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '4px 8px 4px 10px',
      background: 'rgba(255,255,255,0.06)',
      border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 8, color: '#fff',
      fontFamily: 'var(--font-mono)', fontSize: 12, letterSpacing: '0.02em',
    }}>
      {children}
      {onRemove && (
        <button onClick={onRemove} aria-label="Remove" style={{
          background: 'transparent', border: 'none', color: 'rgba(255,255,255,0.5)',
          cursor: 'pointer', padding: 0, fontSize: 14, lineHeight: 1,
        }}>×</button>
      )}
    </span>
  );
}

function PeriodPill({ active, label, onClick }) {
  return (
    <button onClick={onClick} style={{
      padding: '6px 12px',
      background: active ? 'rgba(255,255,255,0.12)' : 'transparent',
      border: '1px solid',
      borderColor: active ? 'rgba(255,255,255,0.16)' : 'rgba(255,255,255,0.06)',
      borderRadius: 8,
      color: active ? '#fff' : 'rgba(255,255,255,0.4)',
      fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.06em',
      textTransform: 'uppercase', cursor: 'pointer',
      transition: 'all 150ms',
    }}>{label}</button>
  );
}

export function ChartControls({
  symbols, period, normalise, onSymbolsChange, onPeriodChange, onNormaliseChange, onNlSubmit,
}) {
  const [symInput, setSymInput] = useState('');
  const [nlInput, setNlInput] = useState('');

  const addSymbol = () => {
    const v = symInput.trim().toUpperCase();
    if (!v) return;
    if (!TICKER_RE.test(v)) {
      setSymInput('');
      return;
    }
    if (symbols.includes(v) || symbols.length >= 5) {
      setSymInput('');
      return;
    }
    onSymbolsChange([...symbols, v]);
    setSymInput('');
  };

  const removeSymbol = (s) => onSymbolsChange(symbols.filter((x) => x !== s));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        {symbols.map((s) => (
          <Chip key={s} onRemove={() => removeSymbol(s)}>{s}</Chip>
        ))}
        {symbols.length < 5 && (
          <input
            value={symInput}
            onChange={(e) => setSymInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); addSymbol(); }
              if (e.key === 'Backspace' && !symInput && symbols.length) {
                onSymbolsChange(symbols.slice(0, -1));
              }
            }}
            placeholder={symbols.length ? 'Add symbol' : 'INFY, TCS, NIFTY50...'}
            style={{
              minWidth: 120, padding: '4px 8px',
              background: 'transparent',
              border: '1px dashed rgba(255,255,255,0.12)',
              borderRadius: 8, color: '#fff',
              fontFamily: 'var(--font-mono)', fontSize: 12, outline: 'none',
            }}
          />
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 4 }}>
          {PERIODS.map((p) => (
            <PeriodPill key={p} active={period === p} label={p} onClick={() => onPeriodChange(p)} />
          ))}
        </div>

        <div style={{ width: 1, height: 18, background: 'rgba(255,255,255,0.08)' }} />

        <div style={{ display: 'flex', gap: 0, padding: 3, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8 }}>
          {[
            { id: true,  label: 'Normalised %' },
            { id: false, label: 'Price ₹' },
          ].map((opt) => (
            <button key={String(opt.id)} onClick={() => onNormaliseChange(opt.id)} style={{
              padding: '5px 10px', cursor: 'pointer', borderRadius: 6,
              fontSize: 10.5, fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
              background: normalise === opt.id ? 'rgba(255,255,255,0.1)' : 'transparent',
              border: '1px solid',
              borderColor: normalise === opt.id ? 'rgba(255,255,255,0.12)' : 'transparent',
              color: normalise === opt.id ? '#fff' : 'rgba(255,255,255,0.4)',
              transition: 'all 150ms',
            }}>{opt.label}</button>
          ))}
        </div>
      </div>

      <input
        value={nlInput}
        onChange={(e) => setNlInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && nlInput.trim()) {
            e.preventDefault();
            onNlSubmit?.(nlInput.trim());
            setNlInput('');
          }
        }}
        placeholder="or describe what you want to compare..."
        style={{
          padding: '8px 12px',
          background: 'rgba(255,255,255,0.03)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: 10, color: 'rgba(255,255,255,0.85)',
          fontFamily: 'var(--font-ui)', fontSize: 12, outline: 'none',
          transition: 'border-color 150ms',
        }}
        onFocus={(e) => (e.target.style.borderColor = 'rgba(255,255,255,0.16)')}
        onBlur={(e) => (e.target.style.borderColor = 'rgba(255,255,255,0.06)')}
      />
    </div>
  );
}
