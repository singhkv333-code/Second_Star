import { useEffect, useState } from 'react';
import { useKite } from '../../hooks/useKite';

function readUrlFlag() {
  if (typeof window === 'undefined') return null;
  const params = new URLSearchParams(window.location.search);
  const kite = params.get('kite');
  if (!kite) return null;
  const reason = params.get('reason');
  // strip the params so a refresh doesn't replay the toast
  params.delete('kite');
  params.delete('reason');
  const next = params.toString();
  const url = window.location.pathname + (next ? `?${next}` : '') + window.location.hash;
  window.history.replaceState({}, '', url);
  return { kite, reason };
}

export function KiteConnectBanner() {
  const { status, loading, busy, error, connect, disconnect, refresh } = useKite();
  const [toast, setToast] = useState(null);

  useEffect(() => {
    const flag = readUrlFlag();
    if (!flag) return;
    if (flag.kite === 'connected') {
      setToast({ kind: 'ok', text: 'Kite connected.' });
      refresh();
    } else if (flag.kite === 'error') {
      setToast({ kind: 'err', text: `Kite connection failed: ${flag.reason || 'unknown'}` });
    }
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [refresh]);

  const connected = status.connected;

  const dotColor = connected
    ? 'var(--color-profit, #22c55e)'
    : (status.mock_mode ? '#eab308' : 'rgba(255,255,255,0.4)');

  const label = loading
    ? 'Checking Kite…'
    : connected
      ? `Kite${status.mock_mode ? ' (mock)' : ''}: ${status.kite_user_id || 'connected'}`
      : (status.mock_mode ? 'Kite not connected (mock mode)' : 'Kite not connected');

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '10px 24px',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
      background: connected ? 'rgba(34,197,94,0.04)' : 'rgba(234,179,8,0.05)',
      fontSize: 12,
    }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: dotColor,
        boxShadow: `0 0 8px ${dotColor}`,
      }} />
      <span style={{ color: 'rgba(255,255,255,0.8)', letterSpacing: '0.02em' }}>
        {label}
      </span>

      {error && (
        <span style={{ color: 'var(--color-loss, #ef4444)', fontSize: 11 }}>
          {error}
        </span>
      )}
      {toast && (
        <span style={{
          color: toast.kind === 'ok' ? 'var(--color-profit, #22c55e)' : 'var(--color-loss, #ef4444)',
          fontSize: 11,
        }}>{toast.text}</span>
      )}

      <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
        {!connected && (
          <button onClick={connect} disabled={busy || loading} style={btnStyle(true)}>
            {busy ? 'Connecting…' : (status.mock_mode ? 'Connect (mock)' : 'Connect Kite')}
          </button>
        )}
        {connected && (
          <button onClick={disconnect} disabled={busy} style={btnStyle(false)}>
            {busy ? 'Disconnecting…' : 'Disconnect'}
          </button>
        )}
      </div>
    </div>
  );
}

function btnStyle(primary) {
  return {
    padding: '6px 14px',
    fontSize: 11,
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    borderRadius: 6,
    cursor: 'pointer',
    fontFamily: 'var(--font-ui, inherit)',
    border: '1px solid ' + (primary ? 'rgba(255,255,255,0.18)' : 'rgba(239,68,68,0.25)'),
    background: primary ? 'rgba(255,255,255,0.06)' : 'rgba(239,68,68,0.08)',
    color: primary ? '#fff' : 'var(--color-loss, #ef4444)',
    transition: 'all 150ms',
  };
}
