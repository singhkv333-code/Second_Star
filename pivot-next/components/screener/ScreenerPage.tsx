// @ts-nocheck — exact 1:1 port from frontend-quartr/.../ScreenerPage.jsx;
// type-checking is suppressed so the file matches the source verbatim.
"use client";
import { useMemo, useState } from 'react';
import {
  STOCKS, SECTORS, MARKET_CAP_TIERS,
  ETFS, ETF_CATEGORIES,
  INDICES, INDEX_CATEGORIES,
  MUTUAL_FUNDS, FUND_CATEGORIES,
} from './screenerData';

// ── Per-screen config ─────────────────────────────────────
// Each screen declares its universe, table columns, preset chips, and the
// filter inputs the rail should render.

function fmtCr(v) {
  if (v == null) return '—';
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)} L Cr`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)} K Cr`;
  return `${v.toFixed(0)} Cr`;
}
const fmtPct = (v) => v == null ? '—' : `${v.toFixed(2)}%`;
const fmtPct1 = (v) => v == null ? '—' : `${v.toFixed(1)}%`;
const fmtNum1 = (v) => v == null ? '—' : v.toFixed(1);
const fmtNum2 = (v) => v == null ? '—' : v.toFixed(2);
const fmtINR = (v) => v == null ? '—' : `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

const STOCK_PRESETS = [
  { id: 'all',        label: 'All',                       set: () => ({}) },
  { id: 'large',      label: 'Large Cap',                 set: () => ({ mcap_min: 50000 }) },
  { id: 'mid',        label: 'Mid Cap',                   set: () => ({ mcap_min: 10000, mcap_max: 50000 }) },
  { id: 'small',      label: 'Small Cap',                 set: () => ({ mcap_max: 10000 }) },
  { id: 'profitable', label: 'Profitable',                set: () => ({ roe_min: 12 }) },
  { id: 'high_roe',   label: 'High ROE',                  set: () => ({ roe_min: 20 }) },
  { id: 'cheap',      label: 'Cheap (P/E < 20)',          set: () => ({ pe_max: 20 }) },
  { id: 'momentum',   label: 'Momentum (1Y > 20%)',       set: () => ({ ret_min: 20 }) },
  { id: 'dividends',  label: 'Dividends',                 set: () => ({ dy_min: 2 }) },
];

const ETF_PRESETS = [
  { id: 'all',     label: 'All',                set: () => ({}) },
  { id: 'broad',   label: 'Broad equity',       set: () => ({ categories: ['Equity — Broad'] }) },
  { id: 'sector',  label: 'Sector',             set: () => ({ categories: ['Equity — Sector'] }) },
  { id: 'debt',    label: 'Debt',               set: () => ({ categories: ['Debt'] }) },
  { id: 'gold',    label: 'Gold',               set: () => ({ categories: ['Gold'] }) },
  { id: 'cheap',   label: 'Low expense (< 0.20%)', set: () => ({ exp_max: 0.20 }) },
  { id: 'large',   label: 'Large AUM (> ₹5K Cr)', set: () => ({ aum_min: 5000 }) },
];

const INDEX_PRESETS = [
  { id: 'all',     label: 'All',                set: () => ({}) },
  { id: 'broad',   label: 'Broad',              set: () => ({ categories: ['Broad'] }) },
  { id: 'sector',  label: 'Sector',             set: () => ({ categories: ['Sector'] }) },
  { id: 'strat',   label: 'Strategy',           set: () => ({ categories: ['Strategy'] }) },
  { id: 'gainers', label: 'Today\'s gainers',   set: () => ({ day_min: 0 }) },
  { id: 'losers',  label: 'Today\'s losers',    set: () => ({ day_max: 0 }) },
];

const FUND_PRESETS = [
  { id: 'all',      label: 'All',                set: () => ({}) },
  { id: 'equity',   label: 'Equity only',        set: () => ({ categories: FUND_CATEGORIES.filter((c) => c.startsWith('Equity')) }) },
  { id: 'debt',     label: 'Debt only',          set: () => ({ categories: FUND_CATEGORIES.filter((c) => c.startsWith('Debt')) }) },
  { id: 'elss',     label: 'ELSS',               set: () => ({ categories: ['ELSS'] }) },
  { id: 'hybrid',   label: 'Hybrid',             set: () => ({ categories: ['Hybrid'] }) },
  { id: 'cheap',    label: 'Low expense (< 1%)', set: () => ({ exp_max: 1.0 }) },
  { id: 'top_5y',   label: '5Y CAGR > 20%',      set: () => ({ five_y_min: 20 }) },
];

const SCREENS = {
  stocks: {
    id: 'stocks',
    label: 'Stocks',
    universe: STOCKS,
    presets: STOCK_PRESETS,
    columns: [
      { id: 'ticker',         label: 'Symbol',     align: 'left',  type: 'ticker' },
      { id: 'sector',         label: 'Sector',     align: 'left',  type: 'text' },
      { id: 'last',           label: 'Last',       align: 'right', type: 'num',  fmt: fmtINR },
      { id: 'day_change_pct', label: 'Day',        align: 'right', type: 'pct' },
      { id: 'market_cap',     label: 'Mkt Cap',    align: 'right', type: 'num',  fmt: fmtCr },
      { id: 'pe',             label: 'P/E',        align: 'right', type: 'num',  fmt: fmtNum1 },
      { id: 'roe',            label: 'ROE',        align: 'right', type: 'num',  fmt: fmtPct1 },
      { id: 'div_yield',      label: 'Div Yield',  align: 'right', type: 'num',  fmt: fmtPct },
      { id: 'one_year_pct',   label: '1-Y Return', align: 'right', type: 'pct' },
    ],
    defaultSort: 'market_cap',
    filterShape: {
      sectors: [], mcap_min: '', mcap_max: '',
      pe_max: '', roe_min: '', dy_min: '', ret_min: '',
    },
    filterFn: (row, f) => {
      if (f.sectors.length > 0 && !f.sectors.includes(row.sector)) return false;
      if (f.mcap_min !== '' && row.market_cap < +f.mcap_min) return false;
      if (f.mcap_max !== '' && row.market_cap > +f.mcap_max) return false;
      if (f.pe_max   !== '' && row.pe         > +f.pe_max)   return false;
      if (f.roe_min  !== '' && row.roe        < +f.roe_min)  return false;
      if (f.dy_min   !== '' && row.div_yield  < +f.dy_min)   return false;
      if (f.ret_min  !== '' && row.one_year_pct < +f.ret_min) return false;
      return true;
    },
  },

  etfs: {
    id: 'etfs',
    label: 'ETFs',
    universe: ETFS,
    presets: ETF_PRESETS,
    columns: [
      { id: 'ticker',         label: 'Symbol',     align: 'left',  type: 'ticker' },
      { id: 'category',       label: 'Category',   align: 'left',  type: 'text' },
      { id: 'last',           label: 'NAV',        align: 'right', type: 'num',  fmt: fmtINR },
      { id: 'day_change_pct', label: 'Day',        align: 'right', type: 'pct' },
      { id: 'aum',            label: 'AUM',        align: 'right', type: 'num',  fmt: fmtCr },
      { id: 'expense_ratio',  label: 'Expense',    align: 'right', type: 'num',  fmt: (v) => `${v.toFixed(2)}%` },
      { id: 'tracking_error', label: 'Track Err.', align: 'right', type: 'num',  fmt: (v) => `${v.toFixed(2)}%` },
      { id: 'one_year_pct',   label: '1-Y Return', align: 'right', type: 'pct' },
      { id: 'three_year_pct', label: '3-Y Return', align: 'right', type: 'pct' },
    ],
    defaultSort: 'aum',
    filterShape: {
      categories: [], aum_min: '', exp_max: '', ret_min: '',
    },
    filterFn: (row, f) => {
      if (f.categories.length > 0 && !f.categories.includes(row.category)) return false;
      if (f.aum_min !== '' && row.aum < +f.aum_min) return false;
      if (f.exp_max !== '' && row.expense_ratio > +f.exp_max) return false;
      if (f.ret_min !== '' && row.one_year_pct < +f.ret_min) return false;
      return true;
    },
  },

  indices: {
    id: 'indices',
    label: 'Indices',
    universe: INDICES,
    presets: INDEX_PRESETS,
    columns: [
      { id: 'ticker',         label: 'Index',      align: 'left',  type: 'ticker_plain' },
      { id: 'category',       label: 'Category',   align: 'left',  type: 'text' },
      { id: 'last',           label: 'Last',       align: 'right', type: 'num',  fmt: (v) => v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) },
      { id: 'day_change_pct', label: 'Day',        align: 'right', type: 'pct' },
      { id: 'one_year_pct',   label: '1-Y',        align: 'right', type: 'pct' },
      { id: 'ytd_pct',        label: 'YTD',        align: 'right', type: 'pct' },
      { id: 'pe',             label: 'P/E',        align: 'right', type: 'num',  fmt: fmtNum1 },
      { id: 'dy',             label: 'Div Yield',  align: 'right', type: 'num',  fmt: fmtPct },
      { id: 'members',        label: 'Members',    align: 'right', type: 'num',  fmt: (v) => v == null ? '—' : String(v) },
    ],
    defaultSort: 'one_year_pct',
    filterShape: {
      categories: [], day_min: '', day_max: '', ytd_min: '',
    },
    filterFn: (row, f) => {
      if (f.categories.length > 0 && !f.categories.includes(row.category)) return false;
      if (f.day_min !== '' && row.day_change_pct < +f.day_min) return false;
      if (f.day_max !== '' && row.day_change_pct > +f.day_max) return false;
      if (f.ytd_min !== '' && row.ytd_pct < +f.ytd_min) return false;
      return true;
    },
  },

  funds: {
    id: 'funds',
    label: 'Mutual Funds',
    universe: MUTUAL_FUNDS,
    presets: FUND_PRESETS,
    columns: [
      { id: 'name',           label: 'Fund',       align: 'left',  type: 'fund' },
      { id: 'category',       label: 'Category',   align: 'left',  type: 'text' },
      { id: 'nav',            label: 'NAV',        align: 'right', type: 'num',  fmt: fmtINR },
      { id: 'aum',            label: 'AUM',        align: 'right', type: 'num',  fmt: fmtCr },
      { id: 'expense_ratio',  label: 'Expense',    align: 'right', type: 'num',  fmt: (v) => `${v.toFixed(2)}%` },
      { id: 'one_year_pct',   label: '1-Y',        align: 'right', type: 'pct' },
      { id: 'three_year_pct', label: '3-Y CAGR',   align: 'right', type: 'pct' },
      { id: 'five_year_pct',  label: '5-Y CAGR',   align: 'right', type: 'pct' },
    ],
    defaultSort: 'aum',
    filterShape: {
      categories: [], aum_min: '', exp_max: '', three_y_min: '', five_y_min: '',
    },
    filterFn: (row, f) => {
      if (f.categories.length > 0 && !f.categories.includes(row.category)) return false;
      if (f.aum_min !== '' && row.aum < +f.aum_min) return false;
      if (f.exp_max !== '' && row.expense_ratio > +f.exp_max) return false;
      if (f.three_y_min !== '' && row.three_year_pct < +f.three_y_min) return false;
      if (f.five_y_min !== '' && row.five_year_pct < +f.five_year_pct) return false;
      return true;
    },
  },
};

const SCREEN_ORDER = ['stocks', 'etfs', 'indices', 'funds'];

// ── Component ────────────────────────────────────────────
export function ScreenerPage() {
  const [screenId, setScreenId] = useState('stocks');
  const screen = SCREENS[screenId];

  const [filters, setFilters] = useState(() => ({ ...screen.filterShape }));
  const [activePreset, setActivePreset] = useState('all');
  const [sortKey, setSortKey] = useState(screen.defaultSort);
  const [sortDir, setSortDir] = useState(-1);

  const switchScreen = (id) => {
    if (id === screenId) return;
    const next = SCREENS[id];
    setScreenId(id);
    setFilters({ ...next.filterShape });
    setActivePreset('all');
    setSortKey(next.defaultSort);
    setSortDir(-1);
  };

  const setFilter = (key, value) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setActivePreset(null);
  };
  const toggleListItem = (key, value) => {
    setFilters((prev) => {
      const arr = prev[key] || [];
      return {
        ...prev,
        [key]: arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value],
      };
    });
    setActivePreset(null);
  };
  const reset = () => {
    setFilters({ ...screen.filterShape });
    setActivePreset('all');
  };
  const applyPreset = (p) => {
    setFilters({ ...screen.filterShape, ...p.set() });
    setActivePreset(p.id);
  };

  const results = useMemo(() => {
    let rows = screen.universe.filter((r) => screen.filterFn(r, filters));
    rows = rows.slice().sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'string') return (av || '').localeCompare(bv || '') * sortDir;
      return ((av ?? 0) - (bv ?? 0)) * sortDir;
    });
    return rows;
  }, [screen, filters, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir((d) => -d);
    else { setSortKey(key); setSortDir(-1); }
  };

  const activeFilterCount = countActiveFilters(filters);

  return (
    <div style={{
      flex: 1, minWidth: 0,
      display: 'flex', flexDirection: 'column',
      height: '100%', overflow: 'hidden',
      background: 'var(--bg-base)',
    }}>
      {/* Title row */}
      <div style={{
        padding: '24px 32px 14px',
        display: 'flex', alignItems: 'center', gap: 16,
        flexShrink: 0,
      }}>
        <div>
          <h1 style={{
            margin: 0,
            fontFamily: 'var(--font-serif)',
            fontWeight: 'var(--weight-display)',
            fontSize: 22,
            letterSpacing: '-0.025em',
            color: 'var(--text-primary)',
          }}>
            Screener
          </h1>
          <div style={{
            marginTop: 4,
            fontSize: 12.5,
            color: 'var(--text-tertiary)',
          }}>
            <span style={{ color: 'var(--text-primary)', fontWeight: 'var(--weight-medium)' }}>
              {results.length}
            </span>
            {' '}of {screen.universe.length} {screen.label.toLowerCase()} match
            {activeFilterCount > 0 && ` · ${activeFilterCount} filter${activeFilterCount === 1 ? '' : 's'} active`}
          </div>
        </div>

        <div style={{ flex: 1 }} />

        {/* Screen-type switch (replaces the old search box) */}
        <div style={{
          display: 'inline-flex', gap: 2, padding: 3,
          background: 'var(--bg-primary)',
          border: '1px solid var(--glass-border)',
          borderRadius: 'var(--radius-pill)',
        }}>
          {SCREEN_ORDER.map((id) => {
            const active = screenId === id;
            return (
              <button
                key={id}
                onClick={() => switchScreen(id)}
                style={{
                  padding: '6px 14px',
                  border: 'none',
                  borderRadius: 'var(--radius-pill)',
                  fontFamily: 'var(--font-ui)',
                  fontSize: 12,
                  fontWeight: 'var(--weight-medium)',
                  cursor: 'pointer',
                  background: active ? 'var(--text-primary)' : 'transparent',
                  color: active ? 'var(--bg-primary)' : 'var(--text-secondary)',
                  transition: 'color 0.2s var(--ease-quartr), background-color 0.2s var(--ease-quartr)',
                }}
              >
                {SCREENS[id].label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Preset chips */}
      <div style={{
        padding: '0 32px 14px',
        display: 'flex', flexWrap: 'wrap', gap: 6,
        flexShrink: 0,
      }}>
        {screen.presets.map((p) => {
          const active = activePreset === p.id;
          return (
            <button key={p.id} onClick={() => applyPreset(p)} style={{
              padding: '6px 12px',
              background: active ? 'var(--text-primary)' : 'transparent',
              border: `1px solid ${active ? 'var(--text-primary)' : 'var(--glass-border)'}`,
              borderRadius: 'var(--radius-pill)',
              color: active ? 'var(--bg-primary)' : 'var(--text-secondary)',
              fontFamily: 'var(--font-ui)',
              fontSize: 12,
              fontWeight: 'var(--weight-medium)',
              cursor: 'pointer',
              transition: 'all 0.2s var(--ease-quartr)',
            }}>{p.label}</button>
          );
        })}
      </div>

      {/* Body */}
      <div style={{
        flex: 1, minHeight: 0,
        display: 'grid',
        gridTemplateColumns: '260px minmax(0, 1fr)',
        gap: 16,
        padding: '0 32px 24px',
      }}>
        <FilterRail
          screenId={screenId}
          filters={filters}
          setFilter={setFilter}
          toggleListItem={toggleListItem}
          reset={reset}
        />

        <ResultsTable
          columns={screen.columns}
          rows={results}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={toggleSort}
        />
      </div>
    </div>
  );
}

// ── Filter rail ──────────────────────────────────────────
function FilterRail({ screenId, filters, setFilter, toggleListItem, reset }) {
  return (
    <aside style={{
      minHeight: 0,
      overflowY: 'auto',
      background: 'var(--bg-primary)',
      border: '1px solid var(--glass-border)',
      borderRadius: 'var(--radius-md)',
      padding: 18,
      display: 'flex', flexDirection: 'column', gap: 18,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{
          fontFamily: 'var(--font-display)',
          fontWeight: 'var(--weight-display)',
          fontSize: 13,
          color: 'var(--text-primary)',
          letterSpacing: '-0.01em',
        }}>
          Filters
        </div>
        <button onClick={reset} style={{
          background: 'transparent',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          fontFamily: 'var(--font-ui)',
          fontSize: 11.5,
          color: 'var(--text-tertiary)',
        }}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-tertiary)'; }}
        >
          Reset
        </button>
      </div>

      {screenId === 'stocks' && (
        <>
          <FilterGroup label="Sector">
            <ChipMulti
              options={SECTORS}
              selected={filters.sectors}
              onToggle={(v) => toggleListItem('sectors', v)}
            />
          </FilterGroup>

          <FilterGroup label="Market cap (₹ Cr)">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              <NumInput value={filters.mcap_min} onChange={(v) => setFilter('mcap_min', v)} placeholder="Min" />
              <NumInput value={filters.mcap_max} onChange={(v) => setFilter('mcap_max', v)} placeholder="Max" />
            </div>
            <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
              {MARKET_CAP_TIERS.map(([id, label, lo, hi]) => (
                <button key={id} onClick={() => {
                  setFilter('mcap_min', lo === 0 ? '' : lo);
                  setFilter('mcap_max', hi === Infinity ? '' : hi);
                }} style={tierBtnStyle}>
                  {label}
                </button>
              ))}
            </div>
          </FilterGroup>

          <FilterGroup label="Valuation">
            <Row label="Max P/E"><NumInput value={filters.pe_max} onChange={(v) => setFilter('pe_max', v)} placeholder="—" /></Row>
          </FilterGroup>

          <FilterGroup label="Quality">
            <Row label="Min ROE %"><NumInput value={filters.roe_min} onChange={(v) => setFilter('roe_min', v)} placeholder="—" /></Row>
          </FilterGroup>

          <FilterGroup label="Income">
            <Row label="Min Div yield %"><NumInput value={filters.dy_min} onChange={(v) => setFilter('dy_min', v)} placeholder="—" /></Row>
          </FilterGroup>

          <FilterGroup label="Momentum">
            <Row label="Min 1-Y return %"><NumInput value={filters.ret_min} onChange={(v) => setFilter('ret_min', v)} placeholder="—" /></Row>
          </FilterGroup>
        </>
      )}

      {screenId === 'etfs' && (
        <>
          <FilterGroup label="Category">
            <ChipMulti
              options={ETF_CATEGORIES}
              selected={filters.categories}
              onToggle={(v) => toggleListItem('categories', v)}
            />
          </FilterGroup>

          <FilterGroup label="AUM (₹ Cr)">
            <Row label="Min AUM"><NumInput value={filters.aum_min} onChange={(v) => setFilter('aum_min', v)} placeholder="—" /></Row>
          </FilterGroup>

          <FilterGroup label="Cost">
            <Row label="Max expense %"><NumInput value={filters.exp_max} onChange={(v) => setFilter('exp_max', v)} placeholder="—" /></Row>
          </FilterGroup>

          <FilterGroup label="Performance">
            <Row label="Min 1-Y return %"><NumInput value={filters.ret_min} onChange={(v) => setFilter('ret_min', v)} placeholder="—" /></Row>
          </FilterGroup>
        </>
      )}

      {screenId === 'indices' && (
        <>
          <FilterGroup label="Category">
            <ChipMulti
              options={INDEX_CATEGORIES}
              selected={filters.categories}
              onToggle={(v) => toggleListItem('categories', v)}
            />
          </FilterGroup>

          <FilterGroup label="Today">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              <NumInput value={filters.day_min} onChange={(v) => setFilter('day_min', v)} placeholder="Min %" />
              <NumInput value={filters.day_max} onChange={(v) => setFilter('day_max', v)} placeholder="Max %" />
            </div>
          </FilterGroup>

          <FilterGroup label="Year-to-date">
            <Row label="Min YTD %"><NumInput value={filters.ytd_min} onChange={(v) => setFilter('ytd_min', v)} placeholder="—" /></Row>
          </FilterGroup>
        </>
      )}

      {screenId === 'funds' && (
        <>
          <FilterGroup label="Category">
            <ChipMulti
              options={FUND_CATEGORIES}
              selected={filters.categories}
              onToggle={(v) => toggleListItem('categories', v)}
            />
          </FilterGroup>

          <FilterGroup label="AUM (₹ Cr)">
            <Row label="Min AUM"><NumInput value={filters.aum_min} onChange={(v) => setFilter('aum_min', v)} placeholder="—" /></Row>
          </FilterGroup>

          <FilterGroup label="Cost">
            <Row label="Max expense %"><NumInput value={filters.exp_max} onChange={(v) => setFilter('exp_max', v)} placeholder="—" /></Row>
          </FilterGroup>

          <FilterGroup label="Performance">
            <Row label="Min 3-Y CAGR %"><NumInput value={filters.three_y_min} onChange={(v) => setFilter('three_y_min', v)} placeholder="—" /></Row>
            <Row label="Min 5-Y CAGR %"><NumInput value={filters.five_y_min} onChange={(v) => setFilter('five_y_min', v)} placeholder="—" /></Row>
          </FilterGroup>
        </>
      )}
    </aside>
  );
}

// ── Results table ────────────────────────────────────────
function ResultsTable({ columns, rows, sortKey, sortDir, onSort }) {
  return (
    <div style={{
      minHeight: 0,
      overflowY: 'auto',
      background: 'var(--bg-primary)',
      border: '1px solid var(--glass-border)',
      borderRadius: 'var(--radius-md)',
    }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-ui)' }}>
        <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-primary)', zIndex: 1 }}>
          <tr>
            {columns.map((c) => {
              const active = sortKey === c.id;
              return (
                <th key={c.id}
                  onClick={() => onSort(c.id)}
                  style={{
                    ...th,
                    textAlign: c.align,
                    color: active ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    cursor: 'pointer',
                  }}
                >
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    flexDirection: c.align === 'right' ? 'row-reverse' : 'row',
                  }}>
                    {c.label}
                    {active && (
                      <span style={{ fontSize: 9, opacity: 0.7 }}>
                        {sortDir < 0 ? '▼' : '▲'}
                      </span>
                    )}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} style={{
                padding: '40px 18px',
                textAlign: 'center',
                color: 'var(--text-secondary)',
                fontSize: 13,
              }}>
                Nothing matches your filters.
              </td>
            </tr>
          ) : rows.map((row, i) => (
            <tr key={row.ticker || row.name} style={{
              background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.012)',
            }}>
              {columns.map((c) => {
                const v = row[c.id];
                let cellColor = 'var(--text-primary)';
                let display;

                if (c.type === 'ticker') {
                  display = (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 'var(--weight-medium)' }}>{row.ticker}</span>
                      {row.exch && (
                        <span style={{
                          fontSize: 9,
                          color: 'var(--text-tertiary)',
                          fontFamily: 'var(--font-mono)',
                          letterSpacing: '0.06em',
                        }}>{row.exch}</span>
                      )}
                    </div>
                  );
                } else if (c.type === 'ticker_plain') {
                  display = <span style={{ fontWeight: 'var(--weight-medium)' }}>{row.ticker}</span>;
                } else if (c.type === 'fund') {
                  display = (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                      <span style={{
                        fontWeight: 'var(--weight-medium)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}>
                        {row.name}
                      </span>
                      <span style={{
                        fontSize: 10.5,
                        color: 'var(--text-tertiary)',
                        fontFamily: 'var(--font-mono)',
                        letterSpacing: '0.06em',
                      }}>
                        {row.ticker}
                      </span>
                    </div>
                  );
                } else if (c.type === 'text') {
                  display = <span style={{ color: 'var(--text-secondary)' }}>{v ?? '—'}</span>;
                } else if (c.type === 'pct') {
                  if (v == null) {
                    display = '—';
                  } else {
                    const isPos = v >= 0;
                    cellColor = isPos ? 'var(--color-profit)' : 'var(--color-loss)';
                    display = `${isPos ? '+' : ''}${v.toFixed(2)}%`;
                  }
                } else if (c.fmt) {
                  display = c.fmt(v);
                } else {
                  display = v == null ? '—' : String(v);
                }

                return (
                  <td key={c.id} style={{
                    ...td,
                    textAlign: c.align,
                    color: cellColor,
                    fontFamily: c.align === 'right' ? 'var(--font-mono)' : 'var(--font-ui)',
                  }}>
                    {display}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Filter primitives ────────────────────────────────────
function FilterGroup({ label, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{
        fontSize: 10,
        color: 'var(--text-tertiary)',
        fontWeight: 'var(--weight-medium)',
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
      }}>
        {label}
      </div>
      {children}
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 80px',
      alignItems: 'center',
      gap: 10,
    }}>
      <span style={{
        fontSize: 12,
        color: 'var(--text-secondary)',
      }}>{label}</span>
      {children}
    </div>
  );
}

function ChipMulti({ options, selected, onToggle }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {options.map((o) => {
        const active = selected.includes(o);
        return (
          <button key={o} onClick={() => onToggle(o)} style={{
            padding: '4px 10px',
            background: active ? 'rgba(255,255,255,0.08)' : 'var(--bg-base)',
            border: `1px solid ${active ? 'var(--glass-border-focus)' : 'var(--glass-border)'}`,
            borderRadius: 'var(--radius-pill)',
            color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
            fontFamily: 'var(--font-ui)',
            fontSize: 11,
            fontWeight: 'var(--weight-medium)',
            cursor: 'pointer',
            transition: 'all 0.2s var(--ease-quartr)',
          }}>{o}</button>
        );
      })}
    </div>
  );
}

function NumInput({ value, onChange, placeholder }) {
  return (
    <input
      type="number"
      inputMode="decimal"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        width: '100%',
        padding: '6px 10px',
        background: 'var(--bg-base)',
        border: '1px solid var(--glass-border)',
        borderRadius: 'var(--radius-sm)',
        color: 'var(--text-primary)',
        fontFamily: 'var(--font-mono)',
        fontSize: 12,
        outline: 'none',
        textAlign: 'right',
      }}
      onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--glass-border-focus)'; }}
      onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--glass-border)'; }}
    />
  );
}

const tierBtnStyle = {
  flex: 1,
  padding: '4px 0',
  background: 'transparent',
  border: '1px solid var(--glass-border)',
  borderRadius: 'var(--radius-sm)',
  color: 'var(--text-tertiary)',
  fontFamily: 'var(--font-ui)',
  fontSize: 10.5,
  fontWeight: 'var(--weight-medium)',
  cursor: 'pointer',
  transition: 'all 0.2s var(--ease-quartr)',
};

function countActiveFilters(f) {
  let n = 0;
  for (const [k, v] of Object.entries(f)) {
    if (Array.isArray(v)) { if (v.length > 0) n++; }
    else if (v !== '' && v != null) n++;
  }
  // Treat min+max pairs as a single filter when only one of them is set or
  // both are. The simple counter above is good enough; collapse mcap pair.
  if (f.mcap_min !== '' && f.mcap_max !== '') n--;
  if (f.day_min !== '' && f.day_max !== '') n--;
  return Math.max(0, n);
}

const th = {
  padding: '12px 16px',
  fontSize: 10,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  fontWeight: 'var(--weight-medium)',
  borderBottom: '1px solid var(--glass-border)',
  whiteSpace: 'nowrap',
  userSelect: 'none',
  fontFamily: 'var(--font-ui)',
};

const td = {
  padding: '11px 16px',
  fontSize: 12.5,
  borderBottom: '1px solid var(--glass-border)',
  whiteSpace: 'nowrap',
};
