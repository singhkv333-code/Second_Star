import { useState } from 'react';
import { apiBacktestParse } from '../../api/endpoints';

const ENTRY_SIGNALS = [
  { id: 'rsi_cross_below',         label: 'RSI Crosses Below' },
  { id: 'rsi_cross_above',         label: 'RSI Crosses Above' },
  { id: 'macd_cross_above_signal', label: 'MACD Crosses Above Signal' },
  { id: 'macd_cross_below_signal', label: 'MACD Crosses Below Signal' },
  { id: 'price_cross_above_sma',   label: 'Price Crosses Above SMA' },
  { id: 'price_cross_below_sma',   label: 'Price Crosses Below SMA' },
  { id: 'price_52wk_high',         label: '52-Week High' },
  { id: 'price_52wk_low',          label: '52-Week Low' },
  { id: 'price_above_pct',         label: 'Daily Move Above %' },
  { id: 'price_below_pct',         label: 'Daily Move Below %' },
  { id: 'bb_lower_touch',          label: 'Bollinger Lower Touch' },
  { id: 'calendar',                label: 'Calendar (e.g. Mondays)' },
  { id: 'sip_monthly',             label: 'Monthly SIP' },
];

const EXIT_SIGNALS = [
  { id: 'hold',                    label: 'Hold to End' },
  { id: 'rsi_cross_above',         label: 'RSI Crosses Above' },
  { id: 'rsi_cross_below',         label: 'RSI Crosses Below' },
  { id: 'macd_cross_below_signal', label: 'MACD Crosses Below Signal' },
  { id: 'macd_cross_above_signal', label: 'MACD Crosses Above Signal' },
  { id: 'price_cross_below_sma',   label: 'Price Crosses Below SMA' },
  { id: 'price_cross_above_sma',   label: 'Price Crosses Above SMA' },
  { id: 'n_days',                  label: 'Sell After N Days' },
  { id: 'stop_and_target',         label: 'Stop / Target Only' },
];

const PERIODS = ['1mo', '3mo', '6mo', '1y', '2y', '3y', '5y', 'ytd', 'max'];

const inputStyle = {
  padding: '9px 12px',
  background: 'rgba(255,255,255,0.04)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: 8,
  color: '#fff', fontSize: 13,
  fontFamily: 'var(--font-mono)',
  outline: 'none', width: '100%',
};

const labelStyle = {
  fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase',
  color: 'rgba(255,255,255,0.4)', fontFamily: 'var(--font-ui)',
  marginBottom: 6, display: 'block',
};

function Field({ label, children }) {
  return (
    <label style={{ display: 'block' }}>
      <div style={labelStyle}>{label}</div>
      {children}
    </label>
  );
}

function defaultParamsFor(signalId) {
  switch (signalId) {
    case 'rsi_cross_below': return { period: 14, threshold: 30 };
    case 'rsi_cross_above': return { period: 14, threshold: 70 };
    case 'macd_cross_above_signal':
    case 'macd_cross_below_signal':
      return { fast: 12, slow: 26, signal: 9 };
    case 'price_cross_above_sma':
    case 'price_cross_below_sma':
      return { period: 50 };
    case 'price_above_pct':
    case 'price_below_pct':
      return { pct: 2.0 };
    case 'bb_lower_touch': return { period: 20, std: 2.0 };
    case 'calendar': return { weekday: 0 };
    case 'n_days': return { n_days: 10 };
    default: return {};
  }
}

function ParamFields({ signalId, params, setParams }) {
  const set = (k, v) => setParams({ ...params, [k]: v });
  switch (signalId) {
    case 'rsi_cross_below':
    case 'rsi_cross_above':
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="Period">
            <input type="number" style={inputStyle} value={params.period ?? 14}
                    onChange={(e) => set('period', Number(e.target.value))} />
          </Field>
          <Field label="Threshold">
            <input type="number" style={inputStyle} value={params.threshold ?? 30}
                    onChange={(e) => set('threshold', Number(e.target.value))} />
          </Field>
        </div>
      );
    case 'macd_cross_above_signal':
    case 'macd_cross_below_signal':
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
          <Field label="Fast"><input type="number" style={inputStyle} value={params.fast ?? 12}
              onChange={(e) => set('fast', Number(e.target.value))} /></Field>
          <Field label="Slow"><input type="number" style={inputStyle} value={params.slow ?? 26}
              onChange={(e) => set('slow', Number(e.target.value))} /></Field>
          <Field label="Signal"><input type="number" style={inputStyle} value={params.signal ?? 9}
              onChange={(e) => set('signal', Number(e.target.value))} /></Field>
        </div>
      );
    case 'price_cross_above_sma':
    case 'price_cross_below_sma':
      return (
        <Field label="SMA Period">
          <input type="number" style={inputStyle} value={params.period ?? 50}
                  onChange={(e) => set('period', Number(e.target.value))} />
        </Field>
      );
    case 'bb_lower_touch':
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <Field label="Period"><input type="number" style={inputStyle} value={params.period ?? 20}
              onChange={(e) => set('period', Number(e.target.value))} /></Field>
          <Field label="Std Dev"><input type="number" step="0.1" style={inputStyle} value={params.std ?? 2}
              onChange={(e) => set('std', Number(e.target.value))} /></Field>
        </div>
      );
    case 'price_above_pct':
    case 'price_below_pct':
      return (
        <Field label="Move %">
          <input type="number" step="0.1" style={inputStyle} value={params.pct ?? 2}
                  onChange={(e) => set('pct', Number(e.target.value))} />
        </Field>
      );
    case 'calendar':
      return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
          <Field label="Weekday">
            <select style={inputStyle} value={params.weekday ?? 0}
                    onChange={(e) => set('weekday', Number(e.target.value))}>
              <option value={0}>Monday</option>
              <option value={1}>Tuesday</option>
              <option value={2}>Wednesday</option>
              <option value={3}>Thursday</option>
              <option value={4}>Friday</option>
            </select>
          </Field>
          <Field label="Price vs SMA">
            <select style={inputStyle} value={params.price_condition ?? ''}
                    onChange={(e) => set('price_condition', e.target.value || undefined)}>
              <option value="">— None —</option>
              <option value="above">Above</option>
              <option value="below">Below</option>
            </select>
          </Field>
          <Field label="SMA Period">
            <input type="number" style={inputStyle} value={params.sma_period ?? 50}
                    onChange={(e) => set('sma_period', Number(e.target.value))} />
          </Field>
        </div>
      );
    case 'n_days':
      return (
        <Field label="Number of Days">
          <input type="number" style={inputStyle} value={params.n_days ?? 10}
                  onChange={(e) => set('n_days', Number(e.target.value))} />
        </Field>
      );
    default:
      return null;
  }
}

export function BacktestForm({ onRun, isRunning }) {
  const [nlInput, setNlInput] = useState('');
  const [nlBusy, setNlBusy] = useState(false);
  const [nlMessage, setNlMessage] = useState(null);

  const [symbol, setSymbol] = useState('NIFTYBEES');
  const [entrySignal, setEntrySignal] = useState('rsi_cross_below');
  const [entryParams, setEntryParams] = useState(defaultParamsFor('rsi_cross_below'));
  const [exitSignal, setExitSignal] = useState('rsi_cross_above');
  const [exitParams, setExitParams] = useState(defaultParamsFor('rsi_cross_above'));
  const [stopLoss, setStopLoss] = useState('');
  const [takeProfit, setTakeProfit] = useState('');
  const [sizingMode, setSizingMode] = useState('inr');
  const [positionSizeInr, setPositionSizeInr] = useState(50000);
  const [positionSizePct, setPositionSizePct] = useState(10);
  const [startingCapital, setStartingCapital] = useState(500000);
  const [period, setPeriod] = useState('3y');
  const [maxPositions, setMaxPositions] = useState(5);

  const handleEntryChange = (id) => {
    setEntrySignal(id);
    setEntryParams(defaultParamsFor(id));
  };
  const handleExitChange = (id) => {
    setExitSignal(id);
    setExitParams(defaultParamsFor(id));
  };

  const buildStrategy = () => {
    const strategy = {
      symbol: symbol.trim().toUpperCase(),
      entry_signal: entrySignal,
      entry_params: entryParams,
      exit_signal: exitSignal,
      exit_params: exitParams,
      starting_capital: Number(startingCapital),
      period,
      max_positions: Number(maxPositions),
      benchmark: 'NIFTY50',
      stop_loss_pct: stopLoss ? Number(stopLoss) : null,
      take_profit_pct: takeProfit ? Number(takeProfit) : null,
      position_size_inr: sizingMode === 'inr' ? Number(positionSizeInr) : null,
      position_size_pct: sizingMode === 'pct' ? Number(positionSizePct) : null,
    };
    return strategy;
  };

  const onParseNl = async () => {
    if (!nlInput.trim()) return;
    setNlBusy(true);
    setNlMessage(null);
    try {
      const res = await apiBacktestParse({ message: nlInput });
      const data = res.data;
      if (data.status === 'ready' && data.strategy) {
        const s = data.strategy;
        setSymbol(s.symbol || symbol);
        setEntrySignal(s.entry_signal || entrySignal);
        setEntryParams(s.entry_params || {});
        setExitSignal(s.exit_signal || 'hold');
        setExitParams(s.exit_params || {});
        if (s.position_size_inr) {
          setSizingMode('inr');
          setPositionSizeInr(s.position_size_inr);
        }
        if (s.starting_capital) setStartingCapital(s.starting_capital);
        if (s.period) setPeriod(s.period);
        setNlMessage({ kind: 'ok', text: 'Strategy parsed — review and run.' });
      } else if (data.status === 'needs_clarification') {
        setNlMessage({ kind: 'ask', text: data.question || 'Please clarify your strategy.' });
      } else {
        setNlMessage({ kind: 'err', text: 'Could not detect a backtest in that prompt.' });
      }
    } catch (err) {
      setNlMessage({ kind: 'err', text: err?.response?.data?.detail || err.message });
    } finally {
      setNlBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

      {/* NL input */}
      <div>
        <div style={labelStyle}>Describe Your Strategy</div>
        <textarea
          rows={3}
          value={nlInput}
          onChange={(e) => setNlInput(e.target.value)}
          placeholder="e.g. backtest buying RELIANCE every time RSI drops below 30 with ₹50,000 per trade for 3 years"
          style={{
            ...inputStyle, padding: '12px 14px', resize: 'vertical',
            fontFamily: 'var(--font-ui)', fontSize: 13, lineHeight: 1.5,
          }}
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8, gap: 10 }}>
          {nlMessage && (
            <div style={{
              flex: 1, fontSize: 11, fontFamily: 'var(--font-mono)',
              color: nlMessage.kind === 'ok' ? 'var(--color-profit, #22c55e)' :
                     nlMessage.kind === 'ask' ? '#f59e0b' : 'var(--color-loss, #ef4444)',
              alignSelf: 'center',
            }}>{nlMessage.text}</div>
          )}
          <button onClick={onParseNl} disabled={nlBusy || !nlInput.trim()} style={{
            padding: '8px 14px',
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: 8, color: '#fff', fontSize: 12,
            cursor: nlBusy ? 'default' : 'pointer',
            fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
          }}>{nlBusy ? 'Parsing…' : 'Parse Strategy'}</button>
        </div>
      </div>

      <div style={{ height: 1, background: 'rgba(255,255,255,0.06)' }} />

      {/* Symbol */}
      <Field label="Symbol">
        <input style={{ ...inputStyle, textTransform: 'uppercase' }}
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="RELIANCE, INFY, NIFTYBEES…" />
      </Field>

      {/* Entry */}
      <div>
        <Field label="Entry Signal">
          <select style={inputStyle} value={entrySignal}
                  onChange={(e) => handleEntryChange(e.target.value)}>
            {ENTRY_SIGNALS.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </Field>
        <div style={{ marginTop: 10 }}>
          <ParamFields signalId={entrySignal} params={entryParams} setParams={setEntryParams} />
        </div>
      </div>

      {/* Exit */}
      <div>
        <Field label="Exit Signal">
          <select style={inputStyle} value={exitSignal}
                  onChange={(e) => handleExitChange(e.target.value)}>
            {EXIT_SIGNALS.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </Field>
        <div style={{ marginTop: 10 }}>
          <ParamFields signalId={exitSignal} params={exitParams} setParams={setExitParams} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 10 }}>
          <Field label="Stop Loss %">
            <input type="number" step="0.5" style={inputStyle}
                    value={stopLoss} onChange={(e) => setStopLoss(e.target.value)}
                    placeholder="optional" />
          </Field>
          <Field label="Take Profit %">
            <input type="number" step="0.5" style={inputStyle}
                    value={takeProfit} onChange={(e) => setTakeProfit(e.target.value)}
                    placeholder="optional" />
          </Field>
        </div>
      </div>

      {/* Position sizing */}
      <div>
        <div style={labelStyle}>Position Sizing</div>
        <div style={{
          display: 'inline-flex', padding: 3, marginBottom: 10,
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: 8, fontSize: 11,
        }}>
          {[['inr', 'Fixed ₹'], ['pct', '% of Portfolio']].map(([id, label]) => (
            <button key={id} onClick={() => setSizingMode(id)} style={{
              padding: '6px 14px', cursor: 'pointer', borderRadius: 6,
              background: sizingMode === id ? 'rgba(255,255,255,0.08)' : 'transparent',
              color: sizingMode === id ? '#fff' : 'rgba(255,255,255,0.5)',
              border: 'none', fontFamily: 'var(--font-mono)',
            }}>{label}</button>
          ))}
        </div>
        {sizingMode === 'inr' ? (
          <Field label="Amount (₹)">
            <input type="number" style={inputStyle} value={positionSizeInr}
                    onChange={(e) => setPositionSizeInr(Number(e.target.value))} />
          </Field>
        ) : (
          <Field label="Portfolio %">
            <input type="number" step="0.5" style={inputStyle} value={positionSizePct}
                    onChange={(e) => setPositionSizePct(Number(e.target.value))} />
          </Field>
        )}
      </div>

      {/* Test config */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <Field label="Starting Capital ₹">
          <input type="number" style={inputStyle} value={startingCapital}
                  onChange={(e) => setStartingCapital(Number(e.target.value))} />
        </Field>
        <Field label="Max Positions">
          <input type="number" min="1" max="20" style={inputStyle} value={maxPositions}
                  onChange={(e) => setMaxPositions(Number(e.target.value))} />
        </Field>
      </div>

      <div>
        <div style={labelStyle}>Period</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {PERIODS.map((p) => (
            <button key={p} onClick={() => setPeriod(p)} style={{
              padding: '6px 14px', borderRadius: 8,
              background: period === p ? 'rgba(255,255,255,0.1)' : 'rgba(255,255,255,0.04)',
              border: '1px solid',
              borderColor: period === p ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.07)',
              color: period === p ? '#fff' : 'rgba(255,255,255,0.55)',
              fontSize: 11, cursor: 'pointer', fontFamily: 'var(--font-mono)',
              textTransform: 'uppercase', letterSpacing: '0.04em',
            }}>{p}</button>
          ))}
        </div>
      </div>

      {/* Run button */}
      <button onClick={() => onRun(buildStrategy())} disabled={isRunning} style={{
        padding: '14px 18px',
        background: isRunning ? 'rgba(255,255,255,0.04)' : 'rgba(255,255,255,0.08)',
        border: '1px solid rgba(255,255,255,0.15)',
        borderRadius: 10, color: '#fff', fontSize: 14,
        cursor: isRunning ? 'default' : 'pointer',
        fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
      }}>
        {isRunning ? (
          <>
            <span style={{
              width: 12, height: 12, borderRadius: '50%',
              border: '2px solid rgba(255,255,255,0.2)',
              borderTopColor: '#fff',
              animation: 'spin 0.8s linear infinite',
            }} />
            Running…
          </>
        ) : (
          <>Run Backtest →</>
        )}
      </button>
    </div>
  );
}
