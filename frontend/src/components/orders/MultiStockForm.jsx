import { useState } from 'react';
import { GlassCard } from '../ui/GlassCard';

const EMPTY_LEG = { symbol: '', quantity: '', price: '', type: 'MARKET' };

export function MultiStockForm({ onPreviewReady }) {
  const [legs, setLegs] = useState([{ ...EMPTY_LEG }, { ...EMPTY_LEG }]);
  const [loading, setLoading] = useState(false);

  const updateLeg = (i, key, val) =>
    setLegs((prev) => prev.map((l, idx) => idx === i ? { ...l, [key]: val } : l));

  const addLeg = () => setLegs((prev) => [...prev, { ...EMPTY_LEG }]);
  const removeLeg = (i) => setLegs((prev) => prev.filter((_, idx) => idx !== i));

  const handlePreview = async (e) => {
    e.preventDefault();
    setLoading(true);
    const validLegs = legs.filter((l) => l.symbol && l.quantity);
    const preview = {
      isMultiLeg: true,
      legs: validLegs,
      transaction_type: 'BUY',
      tradingsymbol: validLegs.map((l) => l.symbol).join(', '),
      quantity: validLegs.reduce((sum, l) => sum + parseInt(l.quantity || 0), 0),
      order_type: 'MULTI',
      preview_id: 'multi_' + Date.now(),
      explanation: `Multi-stock purchase: ${validLegs.map((l) => `${l.quantity} × ${l.symbol}`).join(', ')}. All orders execute simultaneously.`,
      order_details: {
        estimated_value: validLegs.reduce((sum, l) => sum + (parseFloat(l.price || 0) * parseInt(l.quantity || 0)), 0),
      },
    };
    setLoading(false);
    onPreviewReady(preview);
  };

  const inputStyle = {
    width: '100%', padding: '9px 10px',
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6, color: '#fff', fontSize: 12,
    fontFamily: 'var(--font-mono)', outline: 'none',
  };

  return (
    <GlassCard>
      <div style={{
        fontSize: 12, letterSpacing: '0.1em', textTransform: 'uppercase',
        color: 'rgba(255,255,255,0.35)', marginBottom: 20,
      }}>Multi-Stock Basket Buy</div>

      <form onSubmit={handlePreview}>
        <div style={{
          display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 28px',
          gap: 8, marginBottom: 8,
        }}>
          {['Symbol', 'Qty', 'Price (₹)', 'Type', ''].map((h) => (
            <div key={h} style={{
              fontSize: 10, color: 'rgba(255,255,255,0.3)',
              letterSpacing: '0.1em', textTransform: 'uppercase',
            }}>{h}</div>
          ))}
        </div>

        <div className="stagger" style={{ marginBottom: 12 }}>
          {legs.map((leg, i) => (
            <div key={i} style={{
              display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 28px',
              gap: 8, marginBottom: 8,
            }}>
              <input value={leg.symbol} onChange={(e) => updateLeg(i, 'symbol', e.target.value.toUpperCase())}
                placeholder="INFY" style={inputStyle}
                onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.2)'}
                onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
              />
              <input value={leg.quantity} onChange={(e) => updateLeg(i, 'quantity', e.target.value)}
                placeholder="100" type="number" style={inputStyle}
                onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.2)'}
                onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
              />
              <input value={leg.price} onChange={(e) => updateLeg(i, 'price', e.target.value)}
                placeholder="—" type="number" style={inputStyle}
                onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.2)'}
                onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
              />
              <select value={leg.type} onChange={(e) => updateLeg(i, 'type', e.target.value)}
                style={{...inputStyle, cursor: 'pointer'}}>
                <option value="MARKET">MKT</option>
                <option value="LIMIT">LMT</option>
              </select>
              <button type="button" onClick={() => removeLeg(i)}
                disabled={legs.length <= 1}
                style={{
                  background: 'none', border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: 6, color: 'rgba(255,255,255,0.3)',
                  cursor: legs.length > 1 ? 'pointer' : 'default',
                  fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>×</button>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 10 }}>
          <button type="button" onClick={addLeg} style={{
            flex: 1, padding: '9px',
            background: 'transparent', border: '1px dashed rgba(255,255,255,0.1)',
            borderRadius: 8, color: 'rgba(255,255,255,0.4)', cursor: 'pointer',
            fontSize: 12,
          }}>+ Add Stock</button>

          <button type="submit" disabled={loading} style={{
            flex: 2, padding: '9px',
            background: 'rgba(255,255,255,0.08)',
            border: '1px solid rgba(255,255,255,0.15)',
            borderRadius: 8, color: '#fff', cursor: 'pointer',
            fontSize: 13, fontFamily: 'var(--font-ui)',
            transition: 'all 150ms',
          }}>
            Preview Basket →
          </button>
        </div>
      </form>
    </GlassCard>
  );
}
