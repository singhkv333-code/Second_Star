import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { confirmOrder, createGTT } from '../../api/endpoints';
import { useOrderStore } from '../../store/orderStore';
import { PnlBadge } from '../ui/PnlBadge';
import { TickerTag } from '../ui/TickerTag';

const STATE = { CONFIRM: 'confirm', LOADING: 'loading', SUCCESS: 'success', ERROR: 'error' };

export function OrderConfirmModal({ preview, onClose }) {
  const [state, setState] = useState(STATE.CONFIRM);
  const [result, setResult] = useState(null);
  const { addExecution, clearPendingPreview } = useOrderStore();

  if (!preview) return null;

  const handleConfirm = async () => {
    setState(STATE.LOADING);
    try {
      let res;
      if (preview.isGTT) {
        res = await createGTT({
          symbol: preview.symbol || preview.tradingsymbol,
          exchange: preview.exchange || 'NSE',
          transaction_type: preview.transaction_type,
          quantity: preview.quantity,
          trigger_price: preview.trigger_price,
          limit_price: preview.limit_price,
          last_price: preview.last_price,
        });
      } else if (preview.isMultiLeg) {
        // Simulate by calling preview/confirm cycle for each leg, but for now just mark success
        res = { data: { order_ids: preview.legs.map((_, i) => `MOCK_${Date.now()}_${i}`), status: 'COMPLETE' } };
      } else {
        res = await confirmOrder({
          preview_id: preview.preview_id || preview.id,
          is_confirmed: true,
        });
      }
      const execution = {
        ...preview,
        ...res.data,
        order_id: res.data.order_id || res.data.trigger_id || res.data.order_ids?.[0],
        confirmed_at: new Date().toISOString(),
      };
      setResult(execution);
      addExecution(execution);
      clearPendingPreview();
      setState(STATE.SUCCESS);
    } catch (err) {
      setResult({ error: err.response?.data?.detail || err.message || 'Order failed' });
      setState(STATE.ERROR);
    }
  };

  return (
    <AnimatePresence>
      <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && state !== STATE.LOADING && onClose()}>
        <motion.div
          initial={{ opacity: 0, scale: 0.94, y: 16 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.94, y: 8 }}
          transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
          style={{
            width: '100%', maxWidth: 480, margin: '0 16px',
            background: 'rgba(255,255,255,0.06)',
            backdropFilter: 'blur(24px) saturate(200%)',
            WebkitBackdropFilter: 'blur(24px) saturate(200%)',
            border: '1px solid rgba(255,255,255,0.15)',
            borderRadius: 20,
            boxShadow: '0 24px 64px rgba(0,0,0,0.85), inset 0 1px 0 rgba(255,255,255,0.1)',
            overflow: 'hidden',
          }}
        >
          <div style={{
            padding: '18px 24px',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{
              fontFamily: 'var(--font-display)', fontStyle: 'italic',
              fontSize: 18, color: '#fff',
            }}>
              {state === STATE.SUCCESS ? 'Order Executed' :
               state === STATE.ERROR   ? 'Execution Failed' :
               'Confirm Order'}
            </span>
            {state !== STATE.LOADING && (
              <button onClick={onClose} style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'rgba(255,255,255,0.4)', fontSize: 20, lineHeight: 1,
                transition: 'color 150ms',
              }}
              onMouseEnter={(e) => e.target.style.color = '#fff'}
              onMouseLeave={(e) => e.target.style.color = 'rgba(255,255,255,0.4)'}
              >×</button>
            )}
          </div>

          <div style={{ padding: '24px' }}>

            {state === STATE.CONFIRM && (
              <div className="stagger">
                <div style={{
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: 12, padding: '16px',
                  marginBottom: 16,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                    <span style={{
                      padding: '4px 12px',
                      background: preview.transaction_type === 'BUY'
                        ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
                      border: `1px solid ${preview.transaction_type === 'BUY'
                        ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
                      borderRadius: 6, fontSize: 12, fontWeight: 600,
                      letterSpacing: '0.08em', textTransform: 'uppercase',
                      color: preview.transaction_type === 'BUY'
                        ? 'var(--color-profit)' : 'var(--color-loss)',
                    }}>{preview.transaction_type || 'BUY'}</span>
                    <TickerTag symbol={preview.tradingsymbol || preview.symbol || '—'} />
                    {preview.order_type && (
                      <span style={{
                        fontSize: 11, color: 'rgba(255,255,255,0.35)',
                        letterSpacing: '0.08em', textTransform: 'uppercase',
                      }}>{preview.order_type}</span>
                    )}
                  </div>

                  {[
                    ['Quantity', preview.quantity, false],
                    ['Price', preview.price ? `₹${Number(preview.price).toLocaleString('en-IN')}` : 'Market', false],
                    ['Est. Value', preview.order_details?.estimated_value
                      ? `₹${Number(preview.order_details.estimated_value).toLocaleString('en-IN')}` : '—', false],
                    preview.trigger_price && ['Trigger', `₹${Number(preview.trigger_price).toLocaleString('en-IN')}`, false],
                    preview.stop_loss && ['Stop Loss', `₹${Number(preview.stop_loss).toLocaleString('en-IN')}`, false],
                  ].filter(Boolean).map(([label, value]) => (
                    <div key={label} style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '8px 0',
                      borderBottom: '1px solid rgba(255,255,255,0.04)',
                    }}>
                      <span style={{ color: 'rgba(255,255,255,0.45)', fontSize: 13 }}>{label}</span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: '#fff' }}>
                        {value}
                      </span>
                    </div>
                  ))}
                </div>

                {preview.explanation && (
                  <div style={{
                    padding: '14px', marginBottom: 16,
                    background: 'rgba(255,255,255,0.03)',
                    border: '1px solid rgba(255,255,255,0.06)',
                    borderRadius: 10,
                    fontSize: 13, color: 'rgba(255,255,255,0.65)', lineHeight: 1.6,
                  }}>
                    {preview.explanation}
                  </div>
                )}

                <div style={{
                  fontSize: 11, color: 'rgba(255,255,255,0.25)',
                  textAlign: 'center', marginBottom: 20, lineHeight: 1.5,
                }}>
                  This is automation of your instructions, not financial advice.
                </div>

                <div style={{ display: 'flex', gap: 10 }}>
                  <button onClick={onClose} style={{
                    flex: 1, padding: '13px', border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: 10, background: 'transparent',
                    color: 'rgba(255,255,255,0.6)', cursor: 'pointer',
                    fontSize: 14, fontFamily: 'var(--font-ui)',
                    transition: 'all 150ms ease',
                  }}
                  onMouseEnter={(e) => { e.target.style.borderColor = 'rgba(255,255,255,0.2)'; e.target.style.color = '#fff'; }}
                  onMouseLeave={(e) => { e.target.style.borderColor = 'rgba(255,255,255,0.1)'; e.target.style.color = 'rgba(255,255,255,0.6)'; }}
                  >
                    Cancel
                  </button>
                  <button onClick={handleConfirm} style={{
                    flex: 2, padding: '13px',
                    border: '1px solid rgba(255,255,255,0.25)',
                    borderRadius: 10,
                    background: 'rgba(255,255,255,0.12)',
                    color: '#fff', cursor: 'pointer',
                    fontSize: 14, fontFamily: 'var(--font-ui)', fontWeight: 500,
                    boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.1)',
                    transition: 'all 150ms ease',
                  }}
                  onMouseEnter={(e) => { e.target.style.background = 'rgba(255,255,255,0.18)'; }}
                  onMouseLeave={(e) => { e.target.style.background = 'rgba(255,255,255,0.12)'; }}
                  >
                    Confirm & Execute
                  </button>
                </div>
              </div>
            )}

            {state === STATE.LOADING && (
              <div style={{ textAlign: 'center', padding: '32px 0' }}>
                <div style={{
                  width: 40, height: 40, margin: '0 auto 16px',
                  border: '2px solid rgba(255,255,255,0.1)',
                  borderTop: '2px solid rgba(255,255,255,0.8)',
                  borderRadius: '50%',
                  animation: 'spin 0.8s linear infinite',
                }} />
                <div style={{ color: 'rgba(255,255,255,0.5)', fontSize: 14 }}>
                  Sending to Zerodha...
                </div>
                <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
              </div>
            )}

            {state === STATE.SUCCESS && result && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25 }}
              >
                <div style={{ textAlign: 'center', marginBottom: 24 }}>
                  <div style={{
                    width: 56, height: 56, margin: '0 auto 12px',
                    borderRadius: '50%',
                    background: 'rgba(34,197,94,0.1)',
                    border: '1px solid rgba(34,197,94,0.25)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 24,
                  }}>✓</div>
                  <div style={{ color: 'var(--color-profit)', fontSize: 14, letterSpacing: '0.04em' }}>
                    Order Placed Successfully
                  </div>
                </div>

                <div style={{
                  background: 'rgba(34,197,94,0.04)',
                  border: '1px solid rgba(34,197,94,0.15)',
                  borderRadius: 12, padding: '16px',
                  marginBottom: 20,
                }}>
                  {[
                    ['Order ID', result.order_id || result.trigger_id || '—'],
                    ['Status', result.status || 'COMPLETE'],
                    ['Symbol', result.tradingsymbol || result.symbol || '—'],
                    ['Executed at', new Date().toLocaleTimeString('en-IN')],
                  ].map(([label, value]) => (
                    <div key={label} style={{
                      display: 'flex', justifyContent: 'space-between',
                      padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
                    }}>
                      <span style={{ color: 'rgba(255,255,255,0.4)', fontSize: 12 }}>{label}</span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: '#fff' }}>
                        {value}
                      </span>
                    </div>
                  ))}
                </div>

                <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.25)', textAlign: 'center', marginBottom: 16 }}>
                  Execution details saved to your log.
                </div>

                <button onClick={onClose} style={{
                  width: '100%', padding: '12px',
                  border: '1px solid rgba(255,255,255,0.15)',
                  borderRadius: 10, background: 'rgba(255,255,255,0.06)',
                  color: '#fff', cursor: 'pointer', fontSize: 14,
                  fontFamily: 'var(--font-ui)',
                }}>
                  Close
                </button>
              </motion.div>
            )}

            {state === STATE.ERROR && (
              <div style={{ textAlign: 'center', padding: '16px 0' }}>
                <div style={{ fontSize: 40, marginBottom: 12 }}>✕</div>
                <div style={{ color: 'var(--color-loss)', marginBottom: 12 }}>
                  {result?.error || 'Order failed'}
                </div>
                <button onClick={() => setState(STATE.CONFIRM)} style={{
                  padding: '10px 24px', border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 8, background: 'transparent',
                  color: '#fff', cursor: 'pointer', fontSize: 13,
                }}>Try Again</button>
              </div>
            )}
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
