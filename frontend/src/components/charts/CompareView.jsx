import { useEffect, useRef, useState } from 'react';
import { GlassCard, GlassSection } from '../ui/GlassCard';
import { ChartControls } from './ChartControls';
import { CompareChart } from './CompareChart';
import { apiCompare, sendChat } from '../../api/endpoints';

const QUICK_STARTS = [
  { label: 'Nifty vs Sensex — 1Y',     symbols: ['NIFTY50', 'SENSEX'],         period: '1y', normalise: true },
  { label: 'INFY vs TCS — 6M',         symbols: ['INFY', 'TCS'],               period: '6m', normalise: true },
  { label: 'HDFCBANK vs ICICIBANK — 1Y', symbols: ['HDFCBANK', 'ICICIBANK'],   period: '1y', normalise: true },
];

function pickChartType(symbols) {
  return symbols.length >= 2 ? 'comparison' : 'single';
}

function ChartSkeleton() {
  return (
    <div style={{
      position: 'relative', height: 320, overflow: 'hidden',
      borderRadius: 8,
    }}>
      <style>{`
        @keyframes pivotPulse {
          0%, 100% { opacity: 0.10; transform: translateX(0); }
          50%      { opacity: 0.25; transform: translateX(8px); }
        }
      `}</style>
      {[0, 1, 2].map((i) => (
        <svg key={i} viewBox="0 0 400 60" preserveAspectRatio="none" style={{
          position: 'absolute', left: 0, right: 0,
          top: `${30 + i * 80}px`, width: '100%', height: 60,
          animation: `pivotPulse 1.6s ease-in-out ${i * 0.25}s infinite`,
        }}>
          <path
            d={`M0,${30 + i * 4} C 50,${i * 6} 100,${50 - i * 6} 150,${20 + i * 5} S 250,${10 + i * 8} 320,${40 - i * 4} S 400,${20 + i * 4} 400,${30 + i * 3}`}
            stroke={`rgba(255,255,255,${0.20 - i * 0.05})`} strokeWidth="1.5" fill="none"
          />
        </svg>
      ))}
    </div>
  );
}

export default function CompareView({ seed }) {
  const [symbols, setSymbols]     = useState([]);
  const [period,  setPeriod]      = useState('6m');
  const [normalise, setNormalise] = useState(true);
  const [data,    setData]        = useState(null);
  const [loading, setLoading]     = useState(false);
  const [error,   setError]       = useState(null);
  const debounceRef = useRef(null);
  const requestSeq  = useRef(0);
  const lastSeedTs  = useRef(0);

  useEffect(() => {
    if (!seed?.data || seed.ts === lastSeedTs.current) return;
    lastSeedTs.current = seed.ts;
    const chart = seed.data;
    /* eslint-disable react-hooks/set-state-in-effect */
    if (chart.symbols?.length) setSymbols(chart.symbols);
    if (chart.period) setPeriod(chart.period);
    setNormalise(chart.chart_type !== 'single');
    setData(chart);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [seed]);

  const fetchCompare = async (syms, per, norm) => {
    if (!syms.length) {
      setData(null); setError(null); return;
    }
    setLoading(true); setError(null);
    const seq = ++requestSeq.current;
    try {
      const chartType = pickChartType(syms);
      const res = await apiCompare({
        symbols: syms,
        period: per,
        start_date: null,
        end_date: null,
        chart_type: chartType,
        normalise: chartType === 'comparison' ? true : norm,
        sip_amount: null,
      });
      if (seq !== requestSeq.current) return;
      setData(res.data);
    } catch (err) {
      if (seq !== requestSeq.current) return;
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || 'Unknown error';
      setError(status ? `HTTP ${status}: ${detail}` : detail);
      setData(null);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  };

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchCompare(symbols, period, normalise);
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [symbols, period, normalise]);

  const onNlSubmit = async (text) => {
    setLoading(true); setError(null);
    const seq = ++requestSeq.current;
    try {
      const res = await sendChat([{ role: 'user', content: text }]);
      const chart = res?.data?.chart_data;
      if (seq !== requestSeq.current) return;
      if (chart?.symbols?.length) {
        setSymbols(chart.symbols);
        setPeriod(chart.period || period);
        setNormalise(chart.chart_type !== 'single');
        setData(chart);
      } else {
        setError('Could not parse — type symbols above instead.');
      }
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setError(err?.response?.data?.detail || err?.message || 'Parse failed');
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  };

  const applyQuickStart = (qs) => {
    setSymbols(qs.symbols);
    setPeriod(qs.period);
    setNormalise(qs.normalise);
  };

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{
          margin: 0, fontFamily: 'var(--font-display)', fontStyle: 'italic',
          fontSize: 24, color: '#fff', fontWeight: 400,
        }}>Compare</h1>
        <div style={{
          marginTop: 4, fontSize: 12, color: 'rgba(255,255,255,0.4)',
          fontFamily: 'var(--font-ui)', letterSpacing: '0.02em',
        }}>Price history and performance comparison</div>
      </div>

      <GlassSection>
        <GlassCard padding="16px">
          <ChartControls
            symbols={symbols}
            period={period}
            normalise={normalise}
            onSymbolsChange={setSymbols}
            onPeriodChange={setPeriod}
            onNormaliseChange={setNormalise}
            onNlSubmit={onNlSubmit}
          />
        </GlassCard>
      </GlassSection>

      <GlassSection>
        <GlassCard padding="20px">
          {symbols.length === 0 ? (
            <div>
              <div style={{
                position: 'relative', height: 280,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <span style={{
                  fontFamily: 'var(--font-display)', fontStyle: 'italic',
                  color: 'rgba(255,255,255,0.18)', fontSize: 22,
                }}>Enter symbols above to compare</span>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                {QUICK_STARTS.map((qs) => (
                  <button key={qs.label} onClick={() => applyQuickStart(qs)} style={{
                    padding: '8px 14px',
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 10,
                    color: 'rgba(255,255,255,0.7)',
                    fontFamily: 'var(--font-mono)', fontSize: 11,
                    letterSpacing: '0.04em', cursor: 'pointer',
                    transition: 'all 150ms',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.07)'; e.currentTarget.style.color = '#fff'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; e.currentTarget.style.color = 'rgba(255,255,255,0.7)'; }}>
                    {qs.label}
                  </button>
                ))}
              </div>
            </div>
          ) : loading && !data ? (
            <ChartSkeleton />
          ) : error ? (
            <div style={{
              height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--color-loss, #ef4444)',
              fontFamily: 'var(--font-mono)', fontSize: 12,
            }}>{error}</div>
          ) : data ? (
            <CompareChart data={data} height={320} showStats={true} />
          ) : (
            <ChartSkeleton />
          )}
        </GlassCard>
      </GlassSection>

      <div style={{
        marginTop: 8, fontSize: 10, color: 'rgba(255,255,255,0.25)',
        fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
      }}>
        Data: yfinance · 15-min delayed · Not financial advice
      </div>
    </div>
  );
}
