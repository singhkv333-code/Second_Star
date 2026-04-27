import { useState } from 'react';
import { Sidebar } from '../components/layout/Sidebar';
import { SummaryStrip } from '../components/portfolio/SummaryStrip';
import { HoldingsTable } from '../components/portfolio/HoldingsTable';
import { ChatPane } from '../components/chat/ChatPane';
import { GTTOrderForm } from '../components/orders/GTTOrderForm';
import { MultiStockForm } from '../components/orders/MultiStockForm';
import { DipBuyForm } from '../components/orders/DipBuyForm';
import { OrderConfirmModal } from '../components/orders/OrderConfirmModal';
import { usePortfolio } from '../hooks/usePortfolio';
import { useOrderStore } from '../store/orderStore';
import { GlassCard, GlassSection } from '../components/ui/GlassCard';

const ORDER_TABS = ['GTT Order', 'Multi-Stock', 'Dip Buyer'];

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('chat');
  const [orderSubTab, setOrderSubTab] = useState('GTT Order');
  const [pendingPreview, setPendingPreview] = useState(null);
  const { executionLog } = useOrderStore();
  usePortfolio(true);

  const handlePreviewReady = (preview) => setPendingPreview(preview);

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: '#000' }}>

      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        <SummaryStrip />

        <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>

          {activeTab === 'chat' && (
            <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
              <div style={{ flex: 1, overflow: 'hidden', borderRight: '1px solid rgba(255,255,255,0.06)' }}>
                <ChatPane onOrderPreview={handlePreviewReady} />
              </div>
              <div style={{ width: 320, overflowY: 'auto', padding: '20px' }}>
                <GlassSection label="Quick Actions">
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {[
                      ['Set up NIFTYBEES SIP ₹5,000', '⊙'],
                      ['Portfolio health scan', '◈'],
                      ['SafeGrow: protect ₹1L for 12 months', '◆'],
                      ['Dip buy HDFC Bank at -5%', '↓'],
                    ].map(([label, icon]) => (
                      <button key={label} style={{
                        padding: '10px 14px', textAlign: 'left',
                        background: 'rgba(255,255,255,0.03)',
                        border: '1px solid rgba(255,255,255,0.06)',
                        borderRadius: 8, color: 'rgba(255,255,255,0.6)',
                        cursor: 'pointer', fontSize: 12,
                        display: 'flex', alignItems: 'center', gap: 10,
                        transition: 'all 150ms',
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.06)'; e.currentTarget.style.color = '#fff'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.03)'; e.currentTarget.style.color = 'rgba(255,255,255,0.6)'; }}
                      >
                        <span style={{ opacity: 0.5 }}>{icon}</span>{label}
                      </button>
                    ))}
                  </div>
                </GlassSection>
              </div>
            </div>
          )}

          {activeTab === 'portfolio' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>
              <GlassSection label="Holdings">
                <GlassCard padding="0" style={{ overflow: 'hidden' }}>
                  <HoldingsTable />
                </GlassCard>
              </GlassSection>
            </div>
          )}

          {activeTab === 'orders' && (
            <div style={{ flex: 1, overflowY: 'auto', padding: '24px', display: 'flex', gap: 20 }}>
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div style={{
                  display: 'flex', gap: 0, padding: 3,
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.06)',
                  borderRadius: 10,
                }}>
                  {ORDER_TABS.map((t) => (
                    <button key={t} onClick={() => setOrderSubTab(t)} style={{
                      flex: 1, padding: '9px', cursor: 'pointer',
                      borderRadius: 8, fontSize: 12, fontFamily: 'var(--font-ui)',
                      background: orderSubTab === t ? 'rgba(255,255,255,0.08)' : 'transparent',
                      color: orderSubTab === t ? '#fff' : 'rgba(255,255,255,0.35)',
                      transition: 'all 150ms',
                      border: orderSubTab === t ? '1px solid rgba(255,255,255,0.1)' : '1px solid transparent',
                    }}>{t}</button>
                  ))}
                </div>

                {orderSubTab === 'GTT Order'    && <GTTOrderForm onPreviewReady={handlePreviewReady} />}
                {orderSubTab === 'Multi-Stock'  && <MultiStockForm onPreviewReady={handlePreviewReady} />}
                {orderSubTab === 'Dip Buyer'    && <DipBuyForm onPreviewReady={handlePreviewReady} />}
              </div>

              <div style={{ width: 340 }}>
                <GlassSection label="Execution Log">
                  <GlassCard padding="0" style={{ overflow: 'hidden', maxHeight: 600 }}>
                    {executionLog.length === 0 ? (
                      <div style={{ padding: '32px', textAlign: 'center', color: 'rgba(255,255,255,0.2)', fontSize: 13 }}>
                        No executions yet
                      </div>
                    ) : (
                      <div style={{ overflowY: 'auto', maxHeight: 600 }}>
                        {executionLog.map((ex) => (
                          <div key={ex.id} style={{
                            padding: '14px 16px',
                            borderBottom: '1px solid rgba(255,255,255,0.04)',
                            transition: 'background 150ms',
                          }}
                          onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.03)'}
                          onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                              <span style={{
                                fontFamily: 'var(--font-mono)', fontSize: 13, color: '#fff', fontWeight: 500,
                              }}>{ex.tradingsymbol || ex.symbol || '—'}</span>
                              <span style={{
                                fontSize: 11, color: ex.status === 'COMPLETE' ? 'var(--color-profit)' : 'rgba(255,255,255,0.4)',
                                letterSpacing: '0.06em',
                              }}>{ex.status || 'COMPLETE'}</span>
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                              <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)' }}>
                                {ex.transaction_type} · {ex.quantity} qty · {ex.order_type || 'MKT'}
                              </span>
                              <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>
                                {ex.order_id}
                              </span>
                            </div>
                            <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', marginTop: 3 }}>
                              {new Date(ex.executed_at).toLocaleString('en-IN')}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </GlassCard>
                </GlassSection>
              </div>
            </div>
          )}

          {!['chat', 'portfolio', 'orders'].includes(activeTab) && (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
              <div style={{ fontFamily: 'var(--font-display)', fontStyle: 'italic', fontSize: 24, color: 'rgba(255,255,255,0.2)' }}>
                {activeTab.charAt(0).toUpperCase() + activeTab.slice(1)}
              </div>
              <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.15)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                Coming in next build
              </div>
            </div>
          )}
        </div>
      </div>

      {pendingPreview && (
        <OrderConfirmModal
          preview={pendingPreview}
          onClose={() => setPendingPreview(null)}
        />
      )}
    </div>
  );
}
