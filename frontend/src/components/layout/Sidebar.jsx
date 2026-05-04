import { useAuthStore } from '../../store/authStore';

const NAV = [
  { id: 'chat',      label: 'Chat',       icon: '◎' },
  { id: 'portfolio', label: 'Portfolio',  icon: '◈' },
  { id: 'orders',    label: 'Orders',     icon: '◇' },
  { id: 'products',  label: 'Products',   icon: '◆' },
  { id: 'compare',   label: 'Compare',    icon: '⊠' },
  { id: 'backtest',  label: 'Backtest',   icon: '◐' },
  { id: 'strategies',label: 'Strategies', icon: '◉' },
  { id: 'sips',      label: 'SIPs',       icon: '⊙' },
  { id: 'history',   label: 'History',    icon: '≡' },
];

export function Sidebar({ activeTab, setActiveTab }) {
  const { logout } = useAuthStore();

  return (
    <div style={{
      width: 64, minHeight: '100vh',
      background: '#080808',
      borderRight: '1px solid rgba(255,255,255,0.06)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', padding: '20px 0',
    }}>
      <div style={{
        fontFamily: 'var(--font-display)', fontStyle: 'italic',
        fontSize: 20, color: '#fff', marginBottom: 32,
        cursor: 'default',
      }}>P</div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4, width: '100%', padding: '0 8px' }}>
        {NAV.map((item) => (
          <button key={item.id}
            onClick={() => setActiveTab(item.id)}
            title={item.label}
            style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              gap: 4, padding: '10px 6px', cursor: 'pointer',
              borderRadius: 10, width: '100%',
              background: activeTab === item.id ? 'rgba(255,255,255,0.08)' : 'transparent',
              color: activeTab === item.id ? '#fff' : 'rgba(255,255,255,0.3)',
              boxShadow: activeTab === item.id ? 'inset 0 1px 0 rgba(255,255,255,0.06)' : 'none',
              border: activeTab === item.id ? '1px solid rgba(255,255,255,0.1)' : '1px solid transparent',
              transition: 'all 150ms ease',
            }}
            onMouseEnter={(e) => { if (activeTab !== item.id) { e.currentTarget.style.color = 'rgba(255,255,255,0.7)'; e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}}
            onMouseLeave={(e) => { if (activeTab !== item.id) { e.currentTarget.style.color = 'rgba(255,255,255,0.3)'; e.currentTarget.style.background = 'transparent'; }}}
          >
            <span style={{ fontSize: 16, lineHeight: 1 }}>{item.icon}</span>
            <span style={{ fontSize: 8, letterSpacing: '0.08em', textTransform: 'uppercase', lineHeight: 1 }}>
              {item.label}
            </span>
          </button>
        ))}
      </div>

      <div style={{ padding: '0 8px', width: '100%' }}>
        <button onClick={logout} title="Sign out" style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: '100%', padding: '10px', border: '1px solid transparent',
          borderRadius: 10, background: 'transparent',
          color: 'rgba(255,255,255,0.25)', cursor: 'pointer', fontSize: 16,
          transition: 'all 150ms',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--color-loss)'; e.currentTarget.style.borderColor = 'rgba(239,68,68,0.15)'; e.currentTarget.style.background = 'rgba(239,68,68,0.06)'; }}
        onMouseLeave={(e) => { e.currentTarget.style.color = 'rgba(255,255,255,0.25)'; e.currentTarget.style.borderColor = 'transparent'; e.currentTarget.style.background = 'transparent'; }}
        >⎋</button>
      </div>
    </div>
  );
}
