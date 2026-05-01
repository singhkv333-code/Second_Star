import { useEffect, useState } from 'react';
import { GlassCard, GlassSection } from '../ui/GlassCard';
import { BacktestForm } from './BacktestForm';
import { EquityChart } from './EquityChart';
import { MetricsDashboard } from './MetricsDashboard';
import { TradeLog } from './TradeLog';
import { apiBacktestRun, apiBacktestPresets } from '../../api/endpoints';

const TITLE_STYLE = {
  fontFamily: 'var(--font-display)', fontStyle: 'italic',
  fontSize: 24, color: '#fff', lineHeight: 1.1,
};
const SUBTITLE_STYLE = {
  fontSize: 12, color: 'rgba(255,255,255,0.4)',
  fontFamily: 'var(--font-mono)', marginTop: 4,
};

function ProgressBar() {
  return (
    <div style={{ width: '100%', height: 2, background: 'rgba(255,255,255,0.05)', overflow: 'hidden', borderRadius: 1 }}>
      <div style={{
        width: '40%', height: '100%', background: 'rgba(255,255,255,0.6)',
        animation: 'btsweep 1.4s ease-in-out infinite',
      }} />
      <style>{`
        @keyframes btsweep {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(250%); }
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

function PresetCard({ preset, onClick }) {
  return (
    <button onClick={() => onClick(preset)} style={{
      flex: '0 0 240px', padding: '14px 16px', textAlign: 'left',
      background: 'rgba(255,255,255,0.04)',
      border: '1px solid rgba(255,255,255,0.07)',
      borderRadius: 12, color: '#fff', cursor: 'pointer',
      transition: 'all 150ms', fontFamily: 'var(--font-ui)',
    }}
    onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.08)'; }}
    onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>{preset.name}</div>
      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.55)', lineHeight: 1.45 }}>
        {preset.description}
      </div>
      <div style={{
        marginTop: 10, fontSize: 10,
        color: 'rgba(255,255,255,0.35)',
        fontFamily: 'var(--font-mono)', letterSpacing: '0.06em',
      }}>{preset.strategy?.symbol} · {preset.strategy?.period?.toUpperCase()}</div>
    </button>
  );
}

export function BacktestView({ initialResult = null }) {
  const [state, setState] = useState(initialResult ? 'success' : 'idle'); // idle | loading | success | error
  const [result, setResult] = useState(initialResult);
  const [error, setError] = useState(null);
  const [presets, setPresets] = useState([]);

  useEffect(() => {
    let cancelled = false;
    apiBacktestPresets().then((res) => {
      if (!cancelled) setPresets(res.data || []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // If a new initialResult is provided (e.g. opened from chat), pre-load it
  useEffect(() => {
    if (initialResult) {
      setResult(initialResult);
      setState('success');
    }
  }, [initialResult]);

  const runStrategy = async (strategy) => {
    setState('loading');
    setError(null);
    try {
      const res = await apiBacktestRun({ strategy_definition: strategy });
      setResult(res.data);
      setState('success');
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Backtest failed');
      setState('error');
    }
  };

  const onPresetClick = (preset) => {
    runStrategy(preset.strategy);
  };

  const periodYears = result?.metrics?.test_period_years || 1;

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '24px' }}>
      <div style={{ marginBottom: 24 }}>
        <div style={TITLE_STYLE}>Backtest</div>
        <div style={SUBTITLE_STYLE}>
          Simulate how a trading strategy would have performed historically
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
        <GlassCard padding="20px">
          <BacktestForm onRun={runStrategy} isRunning={state === 'loading'} />
        </GlassCard>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <GlassSection label="Presets">
            <div style={{ display: 'flex', gap: 10, overflowX: 'auto', paddingBottom: 4 }}>
              {presets.length === 0 && (
                <div style={{
                  flex: 1, padding: '20px', fontSize: 12,
                  color: 'rgba(255,255,255,0.3)', fontFamily: 'var(--font-mono)',
                }}>Loading presets…</div>
              )}
              {presets.map((p) => (
                <PresetCard key={p.id} preset={p} onClick={onPresetClick} />
              ))}
            </div>
          </GlassSection>
          {state === 'idle' && (
            <GlassCard>
              <div style={{
                fontSize: 12, color: 'rgba(255,255,255,0.4)',
                fontFamily: 'var(--font-mono)', lineHeight: 1.6,
              }}>
                Pick a preset above for a one-click run, paste a natural-language
                description into the form on the left, or build a strategy
                manually using the dropdowns. Results assume realistic Zerodha
                costs (₹20 brokerage, 0.05% slippage, 0.1% STT on sell side).
              </div>
            </GlassCard>
          )}
        </div>
      </div>

      {state === 'loading' && (
        <GlassCard>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, padding: '12px 4px' }}>
            <ProgressBar />
            <div style={{ fontSize: 14, color: '#fff', fontFamily: 'var(--font-mono)' }}>
              Running simulation…
            </div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)', fontFamily: 'var(--font-mono)' }}>
              Fetching historical data and simulating each trading day. Usually takes 3–8 seconds.
            </div>
          </div>
        </GlassCard>
      )}

      {state === 'error' && (
        <GlassCard>
          <div style={{
            color: 'var(--color-loss, #ef4444)', fontSize: 13,
            fontFamily: 'var(--font-mono)',
          }}>
            ⚠ Backtest failed: {error}
          </div>
        </GlassCard>
      )}

      {state === 'success' && result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          <MetricsDashboard
            metrics={result.metrics || {}}
            strategyDefinition={result.strategy_definition || {}}
            warnings={result.warnings || []}
          />
          <GlassCard>
            <EquityChart
              equityCurve={result.equity_curve || []}
              benchmarkCurve={result.benchmark_curve || []}
              startingCapital={result.strategy_definition?.starting_capital || 500000}
              metrics={result.metrics || {}}
              height={340}
            />
          </GlassCard>
          <GlassCard>
            <TradeLog trades={result.trades || []} />
          </GlassCard>
          <div style={{
            fontSize: 10, color: 'rgba(255,255,255,0.3)',
            fontFamily: 'var(--font-mono)', textAlign: 'center',
            padding: '4px 0 12px',
          }}>
            {result.disclaimer || 'Past performance does not guarantee future results.'}
            {' · '}Data source: {result.data_source || 'yfinance'}
          </div>
        </div>
      )}
    </div>
  );
}

export default BacktestView;
