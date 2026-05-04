import { useState } from 'react';
import { GlassCard } from '../ui/GlassCard';

export function GTTOrderForm({ onPreviewReady }) {
  const [form, setForm] = useState({
    symbol: '',
    exchange: 'NSE',
    transaction_type: 'BUY',
    quantity: '',
    trigger_price: '',
    limit_price: '',
    last_price: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const handlePreview = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const payload = {
        ...form,
        tradingsymbol: form.symbol,
        quantity: parseInt(form.quantity),
        trigger_price: parseFloat(form.trigger_price),
        limit_price: parseFloat(form.limit_price || form.trigger_price),
        last_price: parseFloat(form.last_price || form.trigger_price),
        order_type: 'GTT',
        explanation: `GTT Order: ${form.transaction_type} ${form.quantity} ${form.symbol} when price reaches ₹${Number(form.trigger_price).toLocaleString('en-IN')}. Order will execute automatically.`,
        preview_id: 'gtt_' + Date.now(),
        isGTT: true,
        order_details: { estimated_value: parseFloat(form.trigger_price) * parseInt(form.quantity) },
      };
      onPreviewReady(payload);
    } catch (err) {
      setError(err.response?.data?.detail || 'Preview failed');
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    width: '100%', padding: '10px 12px',
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 8, color: '#fff', fontSize: 13,
    fontFamily: 'var(--font-mono)', outline: 'none',
    transition: 'border-color 150ms ease',
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
      }}>GTT — Price Triggered Order</div>

      <form onSubmit={handlePreview}>
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10, marginBottom: 12 }}>
          <div>
            <label style={labelStyle}>Symbol</label>
            <input value={form.symbol} onChange={set('symbol')} placeholder="INFY" required
              style={inputStyle}
              onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.25)'}
              onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
            />
          </div>
          <div>
            <label style={labelStyle}>Exchange</label>
            <select value={form.exchange} onChange={set('exchange')} style={{...inputStyle, cursor: 'pointer'}}>
              <option value="NSE">NSE</option>
              <option value="BSE">BSE</option>
              <option value="NFO">NFO</option>
            </select>
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={labelStyle}>Action</label>
          <div style={{
            display: 'flex', background: 'rgba(255,255,255,0.04)',
            borderRadius: 8, padding: 3, border: '1px solid rgba(255,255,255,0.06)',
          }}>
            {['BUY', 'SELL'].map((type) => (
              <button key={type} type="button"
                onClick={() => setForm((f) => ({ ...f, transaction_type: type }))}
                style={{
                  flex: 1, padding: '8px',
                  borderRadius: 6, fontSize: 12, fontWeight: 600,
                  letterSpacing: '0.06em',
                  background: form.transaction_type === type
                    ? (type === 'BUY' ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)')
                    : 'transparent',
                  color: form.transaction_type === type
                    ? (type === 'BUY' ? 'var(--color-profit)' : 'var(--color-loss)')
                    : 'rgba(255,255,255,0.3)',
                  cursor: 'pointer',
                  transition: 'all 150ms ease',
                  border: form.transaction_type === type
                    ? `1px solid ${type === 'BUY' ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`
                    : '1px solid transparent',
                }}
              >{type}</button>
            ))}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 12 }}>
          {[
            ['Qty', 'quantity', 'number', '100'],
            ['Trigger ₹', 'trigger_price', 'number', '1450'],
            ['Limit ₹', 'limit_price', 'number', '1445'],
          ].map(([label, key, type, ph]) => (
            <div key={key}>
              <label style={labelStyle}>{label}</label>
              <input value={form[key]} onChange={set(key)} type={type}
                placeholder={ph} style={inputStyle} min="0" step="any"
                onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.25)'}
                onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
              />
            </div>
          ))}
        </div>

        <div style={{ marginBottom: 16 }}>
          <label style={labelStyle}>Current Market Price ₹</label>
          <input value={form.last_price} onChange={set('last_price')} type="number"
            placeholder="1523" style={inputStyle}
            onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.25)'}
            onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
          />
        </div>

        {error && (
          <div style={{
            padding: '10px', marginBottom: 12,
            background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
            borderRadius: 8, color: 'var(--color-loss)', fontSize: 12,
          }}>{error}</div>
        )}

        <button type="submit" disabled={loading} style={{
          width: '100%', padding: '11px',
          background: 'rgba(255,255,255,0.08)',
          border: '1px solid rgba(255,255,255,0.15)',
          borderRadius: 8, color: '#fff', cursor: 'pointer',
          fontSize: 13, fontFamily: 'var(--font-ui)',
          transition: 'all 150ms ease',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.06)',
        }}
        onMouseEnter={(e) => e.target.style.background = 'rgba(255,255,255,0.12)'}
        onMouseLeave={(e) => e.target.style.background = 'rgba(255,255,255,0.08)'}
        >
          {loading ? 'Building...' : 'Preview GTT Order →'}
        </button>
      </form>
    </GlassCard>
  );
}
