import { useState } from 'react';
import { GlassCard } from '../ui/GlassCard';

export function DipBuyForm({ onPreviewReady }) {
  const [form, setForm] = useState({
    symbol: '',
    current_price: '',
    dip_pct: '5',
    budget: '',
    exchange: 'NSE',
  });

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const calculated = (() => {
    const price = parseFloat(form.current_price);
    const dip = parseFloat(form.dip_pct);
    const budget = parseFloat(form.budget);
    if (!price || !dip || !budget) return null;
    const trigger = price * (1 - dip / 100);
    const qty = Math.floor(budget / trigger);
    const total = qty * trigger;
    return { trigger: trigger.toFixed(2), qty, total: total.toFixed(0) };
  })();

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!calculated) return;
    onPreviewReady({
      symbol: form.symbol,
      tradingsymbol: form.symbol,
      exchange: form.exchange,
      transaction_type: 'BUY',
      quantity: calculated.qty,
      trigger_price: parseFloat(calculated.trigger),
      limit_price: parseFloat(calculated.trigger) * 0.995,
      last_price: parseFloat(form.current_price),
      order_type: 'GTT',
      isGTT: true,
      preview_id: 'dip_' + Date.now(),
      explanation: `Dip Buy: Purchase ${calculated.qty} shares of ${form.symbol} if it falls ${form.dip_pct}% from ₹${form.current_price} to ₹${calculated.trigger}. Max spend: ₹${Number(calculated.total).toLocaleString('en-IN')}.`,
      order_details: { estimated_value: parseFloat(calculated.total) },
    });
  };

  const inputStyle = {
    width: '100%', padding: '10px 12px',
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 8, color: '#fff', fontSize: 13,
    fontFamily: 'var(--font-mono)', outline: 'none',
    transition: 'border-color 150ms',
  };
  const labelStyle = {
    display: 'block', marginBottom: 6,
    color: 'rgba(255,255,255,0.4)', fontSize: 10,
    letterSpacing: '0.1em', textTransform: 'uppercase',
  };

  return (
    <GlassCard>
      <div style={{
        fontSize: 12, letterSpacing: '0.1em', textTransform: 'uppercase',
        color: 'rgba(255,255,255,0.35)', marginBottom: 20,
      }}>Dip Buyer — Auto Price Calculator</div>

      <form onSubmit={handleSubmit}>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10, marginBottom: 12 }}>
          <div>
            <label style={labelStyle}>Symbol</label>
            <input value={form.symbol} onChange={set('symbol')} placeholder="RELIANCE"
              required style={inputStyle}
              onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.25)'}
              onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
            />
          </div>
          <div>
            <label style={labelStyle}>Exchange</label>
            <select value={form.exchange} onChange={set('exchange')} style={{...inputStyle, cursor: 'pointer'}}>
              <option>NSE</option><option>BSE</option>
            </select>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 16 }}>
          <div>
            <label style={labelStyle}>Current Price ₹</label>
            <input value={form.current_price} onChange={set('current_price')} type="number"
              placeholder="2950" style={inputStyle}
              onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.25)'}
              onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
            />
          </div>
          <div>
            <label style={labelStyle}>Dip %</label>
            <input value={form.dip_pct} onChange={set('dip_pct')} type="number"
              placeholder="5" min="0.5" max="50" step="0.5" style={inputStyle}
              onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.25)'}
              onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
            />
          </div>
          <div>
            <label style={labelStyle}>Budget ₹</label>
            <input value={form.budget} onChange={set('budget')} type="number"
              placeholder="50000" style={inputStyle}
              onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.25)'}
              onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
            />
          </div>
        </div>

        {calculated ? (
          <div style={{
            padding: '16px',
            background: 'rgba(34,197,94,0.04)',
            border: '1px solid rgba(34,197,94,0.12)',
            borderRadius: 10, marginBottom: 16,
          }}>
            <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 12 }}>
              Calculated Order
            </div>
            {[
              ['Trigger Price', `₹${Number(calculated.trigger).toLocaleString('en-IN')}`],
              ['Quantity', `${calculated.qty} shares`],
              ['Max Spend', `₹${Number(calculated.total).toLocaleString('en-IN')}`],
            ].map(([label, value]) => (
              <div key={label} style={{
                display: 'flex', justifyContent: 'space-between',
                padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
              }}>
                <span style={{ color: 'rgba(255,255,255,0.4)', fontSize: 12 }}>{label}</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-profit)', fontSize: 13 }}>{value}</span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{
            padding: '14px', marginBottom: 16,
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.05)',
            borderRadius: 10, textAlign: 'center',
            color: 'rgba(255,255,255,0.25)', fontSize: 12,
          }}>
            Enter values to calculate trigger price and quantity
          </div>
        )}

        <button type="submit" disabled={!calculated} style={{
          width: '100%', padding: '11px',
          background: calculated ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.03)',
          border: `1px solid ${calculated ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.06)'}`,
          borderRadius: 8, color: calculated ? '#fff' : 'rgba(255,255,255,0.25)',
          cursor: calculated ? 'pointer' : 'not-allowed',
          fontSize: 13, fontFamily: 'var(--font-ui)',
          transition: 'all 150ms',
        }}>
          Set Dip Buy GTT →
        </button>
      </form>
    </GlassCard>
  );
}
