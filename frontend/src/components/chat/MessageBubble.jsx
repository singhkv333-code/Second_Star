import { CompareChartCompact } from '../charts/CompareChart';
import { EquityChart } from '../backtest/EquityChart';

function renderInline(text, keyBase) {
  const parts = [];
  const boldRe = /\*\*([^*]+)\*\*/g;
  let lastIdx = 0;
  let m;
  let i = 0;
  while ((m = boldRe.exec(text)) !== null) {
    if (m.index > lastIdx) parts.push(renderRupees(text.slice(lastIdx, m.index), `${keyBase}-t-${i++}`));
    parts.push(
      <strong key={`${keyBase}-b-${i++}`} style={{ color: '#fff', fontWeight: 600 }}>
        {renderRupees(m[1], `${keyBase}-bs-${i++}`)}
      </strong>
    );
    lastIdx = boldRe.lastIndex;
  }
  if (lastIdx < text.length) parts.push(renderRupees(text.slice(lastIdx), `${keyBase}-t-${i++}`));
  return parts;
}

function renderRupees(text, keyBase) {
  const re = /(₹[\d,]+(?:\.\d+)?)/g;
  const out = [];
  let lastIdx = 0;
  let m;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) out.push(text.slice(lastIdx, m.index));
    out.push(
      <span key={`${keyBase}-r-${i++}`} style={{ fontFamily: 'var(--font-mono)', color: '#fff' }}>
        {m[1]}
      </span>
    );
    lastIdx = re.lastIndex;
  }
  if (lastIdx < text.length) out.push(text.slice(lastIdx));
  return out.length === 1 ? out[0] : out;
}

function renderMarkdown(text) {
  if (!text) return null;
  text = text.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
  text = text.replace(/^#{1,6}\s+/gm, '');
  const lines = text.split('\n').filter((l) => l.trim() !== '');
  return lines.map((line, i) => (
    <p key={i} style={{ margin: i === 0 ? 0 : '8px 0 0', lineHeight: 1.65 }}>
      {renderInline(line, `l${i}`)}
    </p>
  ));
}

function summariseConditions(conditions = []) {
  if (!Array.isArray(conditions) || conditions.length === 0) return '';
  const names = conditions.map((c) => {
    const sig = (c?.signal || '').replace(/_/g, ' ');
    const params = c?.params || {};
    const ks = Object.entries(params).map(([k, v]) => `${k}=${v}`).join(', ');
    return ks ? `${sig} (${ks})` : sig;
  });
  return names.join(' · ');
}

function summariseExit(exitDef = {}) {
  const conds = (exitDef?.conditions || []).filter((c) => c.exit_type !== 'end_of_period');
  if (conds.length === 0) return 'hold to end';
  return conds.map((c) => {
    const t = (c.exit_type || '').replace(/_/g, ' ');
    const p = c.params || {};
    if (c.exit_type === 'stop_loss') return `stop -${p.stop_pct}%`;
    if (c.exit_type === 'take_profit') return `target +${p.target_pct}%`;
    if (c.exit_type === 'trailing_stop') return `trail ${p.trail_pct}%`;
    if (c.exit_type === 'after_n_days') return `after ${p.n_days}d`;
    if (c.exit_type === 'indicator_signal') return `${(p.signal || '').replace(/_/g, ' ')}`;
    return t;
  }).join(', ');
}

function BacktestInline({ data, onOpenBacktest }) {
  const m = data?.metrics || {};
  const sd = data?.strategy_definition || data?.strategy || {};
  const startingCapital = sd.starting_capital || 500_000;
  const trades = data?.trades || [];

  const profitColor = 'var(--color-profit, #22c55e)';
  const lossColor = 'var(--color-loss, #ef4444)';
  const neutral = 'rgba(255,255,255,0.92)';

  const tile = (label, value, color = neutral) => (
    <div style={{
      flex: 1, minWidth: 90,
      padding: '10px 12px',
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.06)',
      borderRadius: 8,
      display: 'flex', flexDirection: 'column', gap: 4,
    }}>
      <div style={{
        fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase',
        color: 'rgba(255,255,255,0.35)', fontFamily: 'var(--font-ui)',
      }}>{label}</div>
      <div style={{
        fontSize: 14, color, fontFamily: 'var(--font-mono)',
      }}>{value}</div>
    </div>
  );

  const fmtPct = (v, signed = false) => {
    if (v == null || isNaN(v)) return '—';
    const s = `${signed && v >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
    return s;
  };
  const fmtNum = (v, digits = 2) => (v == null || isNaN(v) ? '—' : Number(v).toFixed(digits));

  const sr = m.total_return_pct ?? 0;
  const br = m.benchmark_return_pct ?? 0;
  const ap = m.alpha_pct ?? 0;
  const dd = m.max_drawdown_pct ?? 0;
  const wr = m.win_rate_pct ?? 0;
  const sh = m.sharpe_ratio ?? 0;
  const totTrades = m.total_trades ?? trades.length;
  const wins = m.winning_trades ?? 0;
  const losses = Math.max(totTrades - wins, 0);

  const symbol = sd.symbol || 'Strategy';
  const period = (sd.period || '').toString().toUpperCase();
  const entrySummary = summariseConditions(sd.entry?.conditions);
  const exitSummary = summariseExit(sd.exit);
  const headerLine = entrySummary || sd.strategy_description || 'custom strategy';

  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* header strip */}
      <div style={{
        display: 'flex', alignItems: 'baseline', justifyContent: 'space-between',
        gap: 8, padding: '8px 10px',
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.05)',
        borderRadius: 8,
      }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{
            fontSize: 13, color: '#fff', fontFamily: 'var(--font-ui)',
            fontWeight: 500,
          }}>
            {symbol}
            {period && (
              <span style={{
                marginLeft: 8, fontSize: 10,
                color: 'rgba(255,255,255,0.4)',
                fontFamily: 'var(--font-mono)', letterSpacing: '0.06em',
              }}>· {period}</span>
            )}
          </div>
          <div style={{
            fontSize: 11, color: 'rgba(255,255,255,0.5)',
            marginTop: 2, lineHeight: 1.4,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }} title={`Entry: ${headerLine} | Exit: ${exitSummary}`}>
            entry: {headerLine || '—'}  ·  exit: {exitSummary}
          </div>
        </div>
      </div>

      {/* metric tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
        {tile('Strategy', fmtPct(sr, true), sr >= 0 ? profitColor : lossColor)}
        {tile('Benchmark', fmtPct(br, true), br >= 0 ? profitColor : lossColor)}
        {tile('Alpha', fmtPct(ap, true), ap >= 0 ? profitColor : lossColor)}
        {tile('Max DD', fmtPct(dd), lossColor)}
        {tile('Sharpe', fmtNum(sh), sh >= 1 ? '#fff' : 'rgba(255,255,255,0.7)')}
        {tile('Win Rate', `${fmtNum(wr, 0)}%`, '#fff')}
      </div>

      {/* equity chart */}
      <div style={{
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.05)',
        borderRadius: 8, padding: '10px',
      }}>
        <EquityChart
          equityCurve={data.equity_curve || []}
          benchmarkCurve={data.benchmark_curve || []}
          startingCapital={startingCapital}
          metrics={m}
          height={160}
          compact
        />
      </div>

      {/* trades summary */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        gap: 8, fontSize: 11, color: 'rgba(255,255,255,0.55)',
        fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
      }}>
        <span>
          {totTrades} trade{totTrades === 1 ? '' : 's'}
          {totTrades > 0 && (
            <>
              <span style={{ color: profitColor, marginLeft: 8 }}>{wins}W</span>
              <span style={{ color: lossColor, marginLeft: 4 }}>{losses}L</span>
            </>
          )}
        </span>
        {onOpenBacktest && (
          <button onClick={() => onOpenBacktest(data)} style={{
            padding: '4px 0',
            background: 'transparent', border: 'none',
            color: 'rgba(255,255,255,0.55)', cursor: 'pointer',
            fontSize: 11, fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
          }}
            onMouseEnter={(e) => (e.currentTarget.style.color = '#fff')}
            onMouseLeave={(e) => (e.currentTarget.style.color = 'rgba(255,255,255,0.55)')}
          >View full backtest →</button>
        )}
      </div>

      {(data.warnings || []).length > 0 && (
        <div style={{
          fontSize: 11, color: 'rgba(255,200,0,0.7)',
          padding: '6px 10px',
          background: 'rgba(255,200,0,0.05)',
          border: '1px solid rgba(255,200,0,0.15)',
          borderRadius: 6,
        }}>
          {data.warnings.slice(0, 2).join(' · ')}
        </div>
      )}
    </div>
  );
}

function ScreenInline({ data }) {
  const rows = data?.rows || [];
  const leaves = data?.leaf_fields || [];
  const tile = (label, value) => (
    <div style={{
      padding: '6px 10px', background: 'rgba(255,255,255,0.04)',
      border: '1px solid rgba(255,255,255,0.06)', borderRadius: 6,
      fontSize: 11, color: 'rgba(255,255,255,0.85)',
    }}>
      <span style={{ color: 'rgba(255,255,255,0.4)' }}>{label} </span>
      <span style={{ fontFamily: 'var(--font-mono)' }}>{value}</span>
    </div>
  );
  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {tile('expr', data?.expression)}
        {tile('as of', data?.as_of)}
        {tile('matches', data?.n_total)}
      </div>
      {rows.length > 0 && (
        <div style={{
          background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.05)',
          borderRadius: 8, overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
            <thead>
              <tr style={{ background: 'rgba(255,255,255,0.03)' }}>
                <th style={th}>sc_id</th>
                <th style={th}>company</th>
                {leaves.map((lf) => (
                  <th key={lf} style={th}>{lf}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 12).map((r, i) => (
                <tr key={i} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                  <td style={td}>{r.sc_id}</td>
                  <td style={{ ...td, color: 'rgba(255,255,255,0.85)' }}>{r.company_name}</td>
                  {leaves.map((lf) => (
                    <td key={lf} style={td}>
                      {r[`${lf}_val`] == null ? '—' : Number(r[`${lf}_val`]).toFixed(4)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data?.truncated && (
        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', fontFamily: 'var(--font-mono)' }}>
          Showing 12 of {data.n_total}. Use the API or CLI for the full list.
        </div>
      )}
    </div>
  );
}

const th = { padding: '6px 8px', textAlign: 'left', color: 'rgba(255,255,255,0.45)', fontWeight: 500 };
const td = { padding: '6px 8px', color: 'rgba(255,255,255,0.7)' };

function ExprBacktestInline({ data }) {
  const m = data?.metrics || {};
  const profit = 'var(--color-profit, #22c55e)';
  const loss = 'var(--color-loss, #ef4444)';
  const tile = (label, value, color) => (
    <div style={{
      padding: '8px 10px', background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8,
      display: 'flex', flexDirection: 'column', gap: 2,
    }}>
      <div style={{ fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'rgba(255,255,255,0.35)' }}>{label}</div>
      <div style={{ fontSize: 14, color: color || '#fff', fontFamily: 'var(--font-mono)' }}>{value}</div>
    </div>
  );
  const fmt = (v, signed = false) => v == null ? '—' : `${signed && v >= 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
  const cagr = m.cagr_pct ?? 0;
  const dd = m.max_drawdown_pct ?? 0;
  const sh = m.sharpe ?? 0;
  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.55)', fontFamily: 'var(--font-mono)' }}>
        {data.expression} · {data.start} → {data.end} · rebalance {data.rebalance}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
        {tile('CAGR', fmt(cagr, true), cagr >= 0 ? profit : loss)}
        {tile('Total', fmt(m.total_return_pct, true), (m.total_return_pct || 0) >= 0 ? profit : loss)}
        {tile('Max DD', fmt(dd), loss)}
        {tile('Sharpe', sh.toFixed(2))}
      </div>
      {data?.warnings?.length > 0 && (
        <div style={{
          fontSize: 11, color: 'rgba(255,200,0,0.7)',
          padding: '6px 10px', background: 'rgba(255,200,0,0.05)',
          border: '1px solid rgba(255,200,0,0.15)', borderRadius: 6,
        }}>
          {data.warnings.slice(0, 2).join(' · ')}
        </div>
      )}
    </div>
  );
}

export function MessageBubble({ message, onOpenChart, onOpenBacktest }) {
  const isUser = message.role === 'user';

  return (
    <div style={{
      display: 'flex',
      justifyContent: isUser ? 'flex-end' : 'flex-start',
      marginBottom: 16,
    }}>
      {!isUser && (
        <div style={{
          width: 24, height: 24, borderRadius: '50%',
          background: 'rgba(255,255,255,0.06)',
          border: '1px solid rgba(255,255,255,0.1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, color: 'rgba(255,255,255,0.4)',
          marginRight: 10, marginTop: 4, flexShrink: 0,
          fontFamily: 'var(--font-display)', fontStyle: 'italic',
        }}>P</div>
      )}

      <div style={{
        maxWidth: (message.chartData || message.backtestData || message.screenData || message.exprBacktestData) ? '88%' : '72%',
        padding: '12px 16px',
        borderRadius: isUser ? '16px 16px 4px 16px' : '4px 16px 16px 16px',
        background: isUser ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.04)',
        border: '1px solid',
        borderColor: isUser ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.07)',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.04)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        fontSize: 14,
        color: isUser ? '#fff' : 'rgba(255,255,255,0.9)',
        lineHeight: 1.65,
        fontFamily: 'var(--font-ui)',
      }}>
        {isUser ? <span>{message.content}</span> : renderMarkdown(message.content)}

        {message.chartData && (
          <div style={{ marginTop: 12 }}>
            <CompareChartCompact data={message.chartData} height={200} />
            {onOpenChart && (
              <button
                onClick={() => onOpenChart(message.chartData)}
                style={{
                  marginTop: 8, padding: '4px 0',
                  background: 'transparent', border: 'none',
                  color: 'rgba(255,255,255,0.5)', cursor: 'pointer',
                  fontSize: 11, fontFamily: 'var(--font-mono)',
                  letterSpacing: '0.04em', textAlign: 'left',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.color = '#fff')}
                onMouseLeave={(e) => (e.currentTarget.style.color = 'rgba(255,255,255,0.5)')}
              >Open full chart →</button>
            )}
          </div>
        )}

        {message.backtestData && (
          <BacktestInline data={message.backtestData} onOpenBacktest={onOpenBacktest} />
        )}

        {message.screenData && (
          <ScreenInline data={message.screenData} />
        )}

        {message.exprBacktestData && (
          <ExprBacktestInline data={message.exprBacktestData} />
        )}

        {message.requiresClarification && message.intent === 'BACKTEST' && (
          <div style={{
            marginTop: 10,
            padding: '8px 12px',
            background: 'rgba(96,165,250,0.06)',
            border: '1px solid rgba(96,165,250,0.18)',
            borderRadius: 8,
            fontSize: 11, color: 'rgba(150,190,255,0.85)',
            fontFamily: 'var(--font-mono)', letterSpacing: '0.04em',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ opacity: 0.7 }}>⌁</span>
            Backtest needs: {(message.missingParams || []).join(', ') || 'more details'}
          </div>
        )}

        {message.logicCard && (
          <div style={{
            marginTop: 12,
            padding: '9px 12px',
            background: 'rgba(34,197,94,0.05)',
            border: '1px solid rgba(34,197,94,0.15)',
            borderRadius: 8,
            fontSize: 12,
            color: 'rgba(34,197,94,0.85)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ opacity: 0.7 }}>◈</span>
            Strategy ready — confirm in Orders panel
          </div>
        )}

        {message.timestamp && (
          <div style={{
            marginTop: 6, fontSize: 10,
            color: 'rgba(255,255,255,0.2)',
            textAlign: isUser ? 'right' : 'left',
          }}>
            {new Date(message.timestamp).toLocaleTimeString('en-IN', {
              hour: '2-digit', minute: '2-digit',
            })}
          </div>
        )}
      </div>
    </div>
  );
}
