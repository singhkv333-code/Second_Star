import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../store/authStore';
import { authLogin, authRegister } from '../api/endpoints';

export default function Login() {
  const [mode, setMode] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { login } = useAuthStore();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const fn = mode === 'login' ? authLogin : authRegister;
      const res = await fn({ email, password });
      const { access_token, refresh_token, user_id } = res.data;
      login({ email, user_id }, access_token, refresh_token);
      navigate('/');
    } catch (err) {
      setError(err.response?.data?.detail || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh', background: '#000',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'var(--font-ui)',
    }}>
      <div style={{
        position: 'fixed', inset: 0, opacity: 0.03,
        backgroundImage: 'radial-gradient(circle at 1px 1px, white 1px, transparent 0)',
        backgroundSize: '32px 32px',
      }} />

      <div style={{
        width: 400, padding: '48px 40px',
        background: 'rgba(255,255,255,0.04)',
        backdropFilter: 'blur(24px) saturate(180%)',
        WebkitBackdropFilter: 'blur(24px) saturate(180%)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 20,
        boxShadow: '0 32px 80px rgba(0,0,0,0.8), inset 0 1px 0 rgba(255,255,255,0.08)',
        position: 'relative',
      }}>
        <div style={{ marginBottom: 40, textAlign: 'center' }}>
          <div style={{
            fontFamily: 'var(--font-display)', fontStyle: 'italic',
            fontSize: 36, color: '#fff', letterSpacing: '-0.02em',
          }}>Pivot</div>
          <div style={{ color: 'rgba(255,255,255,0.4)', fontSize: 12, marginTop: 6, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            AI Investing Terminal
          </div>
        </div>

        <div style={{
          display: 'flex', gap: 0, marginBottom: 32,
          background: 'rgba(255,255,255,0.04)', borderRadius: 8, padding: 3,
          border: '1px solid rgba(255,255,255,0.06)',
        }}>
          {['login', 'register'].map((m) => (
            <button key={m} onClick={() => setMode(m)} style={{
              flex: 1, padding: '8px 0', border: 'none', cursor: 'pointer',
              borderRadius: 6, fontSize: 13, fontFamily: 'var(--font-ui)',
              transition: 'all 150ms ease',
              background: mode === m ? 'rgba(255,255,255,0.1)' : 'transparent',
              color: mode === m ? '#fff' : 'rgba(255,255,255,0.4)',
              boxShadow: mode === m ? 'inset 0 1px 0 rgba(255,255,255,0.1)' : 'none',
            }}>
              {m === 'login' ? 'Sign In' : 'Register'}
            </button>
          ))}
        </div>

        <form onSubmit={handleSubmit}>
          {['email', 'password'].map((field) => (
            <div key={field} style={{ marginBottom: 16 }}>
              <label style={{
                display: 'block', marginBottom: 6,
                color: 'rgba(255,255,255,0.5)', fontSize: 11,
                letterSpacing: '0.08em', textTransform: 'uppercase',
              }}>{field}</label>
              <input
                type={field}
                value={field === 'email' ? email : password}
                onChange={(e) => field === 'email' ? setEmail(e.target.value) : setPassword(e.target.value)}
                required
                style={{
                  width: '100%', padding: '12px 14px',
                  background: 'rgba(255,255,255,0.04)',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 8, color: '#fff', fontSize: 14,
                  fontFamily: 'var(--font-mono)', outline: 'none',
                  transition: 'border-color 150ms ease',
                }}
                onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.3)'}
                onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.1)'}
                placeholder={field === 'email' ? 'you@example.com' : '••••••••'}
              />
            </div>
          ))}

          {error && (
            <div style={{
              padding: '10px 14px', marginBottom: 16,
              background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
              borderRadius: 8, color: '#EF4444', fontSize: 13,
            }}>{error}</div>
          )}

          <button type="submit" disabled={loading} style={{
            width: '100%', padding: '13px', marginTop: 8,
            background: loading ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.1)',
            border: '1px solid rgba(255,255,255,0.2)',
            borderRadius: 8, color: '#fff', fontSize: 14, fontFamily: 'var(--font-ui)',
            cursor: loading ? 'not-allowed' : 'pointer',
            transition: 'all 150ms ease',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08)',
          }}>
            {loading ? 'Authenticating...' : (mode === 'login' ? 'Sign In' : 'Create Account')}
          </button>
        </form>
      </div>
    </div>
  );
}
