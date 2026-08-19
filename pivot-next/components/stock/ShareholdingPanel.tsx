"use client";

/**
 * Shareholding — who owns the company, and which way they are moving.
 *
 * Same shape as the segment mix above it: the stacked series is the argument,
 * the right-hand column reads the latest column out. That is deliberate — two
 * panels answering "what is this made of, and how is that changing" should not
 * invent two different ways of showing it.
 *
 * The colours are NOT the shared chart ramp. That ramp opens on the product's
 * blue, and every band here is an owner class the reader has to hold in their
 * head across three views (the stack, the split bars, the holder rows), so the
 * mapping is fixed by LABEL rather than by index. A company with no promoter
 * would otherwise shift every other class one colour to the left and quietly
 * repaint the whole section.
 */

import dynamic from "next/dynamic";
import * as React from "react";

import type { ShareholdingResponse } from "@/lib/api";
import { EmptyNote, PanelHead } from "./chrome";
import { num } from "./FinTable";

const EChart = dynamic(() => import("./EChart"), {
  ssr: false,
  loading: () => <div style={{ height: 300 }} />,
});

/** The height of the row both charts live in. Stated once here rather than
 *  left to the content, because neither of the two has an intrinsic height. */
const CHART_H = 330;

/** Fixed by owner class, not by position. */
const OWNER_COLOR: Record<string, string> = {
  "Promoters": "#8A6D3B",
  "Foreign institutions": "#C4643F",
  "Domestic institutions": "#4F8A5B",
  "Non-institutions": "#C0A03C",
  "Non-promoter non-public": "#7A7268",
};
const OWNER_ORDER = [
  "Promoters", "Foreign institutions", "Domestic institutions",
  "Non-institutions", "Non-promoter non-public",
];

/** A child tile, lit off its parent. Mixing the class colour toward the page
 *  rather than giving each sub-category a hue of its own keeps the treemap
 *  readable as four blocks with parts, instead of fifteen unrelated colours
 *  that happen to be adjacent. */
function lighten(hex: string, t: number): string {
  const n = parseInt(hex.slice(1), 16);
  const mix = (c: number) => Math.round(c + (255 - c) * t);
  return `rgb(${mix((n >> 16) & 255)}, ${mix((n >> 8) & 255)}, ${mix(n & 255)})`;
}

/** The holder bucket arrives as an XBRL member name in PascalCase. */
function holderClass(bucket: string | null): string {
  if (!bucket) return "";
  return bucket
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/Or U[Tt][Ii]/, "/ UTI")
    .replace("Institutions Foreign Portfolio Investor One", "FPI category I")
    .replace("Institutions Foreign Portfolio Investor Two", "FPI category II")
    .replace("Mutual Funds", "Mutual fund");
}

function quarterLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  // en-IN abbreviates September as "Sept", which is four characters where
  // every other month is three — enough to make one axis tick sit wider than
  // its neighbours. Clipped to three so the axis stays even.
  const mon = d.toLocaleDateString("en-IN", { month: "short" }).slice(0, 3);
  return `${mon} ${d.toLocaleDateString("en-IN", { year: "2-digit" })}`;
}

export function ShareholdingPanel({
  data,
}: {
  data: ShareholdingResponse;
}): React.ReactElement {
  const quarters = data.quarters ?? [];
  const groups = data.groups ?? [];

  // Which owner classes this company actually files. A class present in one
  // quarter and absent in another still gets a band — the gap is drawn null.
  const classes = React.useMemo(
    () => OWNER_ORDER.filter((c) => quarters.some((q) => typeof q[c] === "number")),
    [quarters],
  );

  const option = React.useMemo(
    () => (quarters.length > 1 ? riverOption(quarters, classes) : null),
    [quarters, classes],
  );

  // The one movement worth naming, computed the same way the mix panel does it.
  const shift = React.useMemo(() => {
    if (quarters.length < 2) return null;
    const last = quarters[quarters.length - 1]!;
    const prev = quarters[quarters.length - 2]!;
    const moves = classes
      .map((c) => {
        const a = last[c], b = prev[c];
        return typeof a === "number" && typeof b === "number"
          ? { name: c, delta: a - b } : null;
      })
      .filter((v): v is { name: string; delta: number } => v !== null)
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    return moves[0] ?? null;
  }, [quarters, classes]);

  // The composition as AREA. The rail here used to be a column of label-and-
  // value rows — four bars and fifteen numbers, which is a table wearing a
  // chart's clothes, and it repeated in text what the streamgraph beside it
  // already draws. A treemap says the same thing as one picture: a promoter
  // block you cannot miss, and every sub-category sized against every other
  // one on the same scale rather than against its own parent.
  const treemap = React.useMemo(() => {
    const nodes = groups.map((g) => {
      const base = OWNER_COLOR[g.label] ?? "#7A7268";
      return {
        name: g.label,
        value: g.pct,
        itemStyle: { color: base },
        // A class with no filed split stays one tile. Splitting it into a
        // single child would draw a border around itself.
        children: g.children.length > 1
          ? g.children
            .filter((c) => c.pct > 0)
            .map((c, i) => ({
              name: c.label,
              value: c.pct,
              itemStyle: { color: lighten(base, 0.22 + i * 0.11) },
            }))
          : undefined,
      };
    });
    if (!nodes.length) return null;
    return {
      tooltip: {
        formatter: (p: { name?: string; value?: number }) =>
          `${p.name} <b>${(p.value ?? 0).toFixed(2)}%</b>`,
      },
      series: [{
        type: "treemap",
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        animationDuration: 260,
        top: 2, bottom: 2, left: 0, right: 0,
        // Sub-categories are drawn, not folded away: leafDepth would collapse
        // them into their parent and the panel would lose the only place the
        // FPI and mutual-fund splits are visible at all.
        levels: [
          { itemStyle: { gapWidth: 3, borderWidth: 0 } },
          { itemStyle: { gapWidth: 1, borderWidth: 0 } },
        ],
        label: {
          show: true,
          position: "insideTopLeft",
          formatter: (p: { name?: string; value?: number }) =>
            `{n|${p.name}}\n{v|${(p.value ?? 0).toFixed(2)}%}`,
          rich: {
            n: { fontSize: 11.5, fontWeight: 500, color: "#fff", lineHeight: 15,
                 fontFamily: "var(--font-ui)" },
            v: { fontSize: 14, fontWeight: 600, color: "#fff", lineHeight: 18,
                 fontFamily: "var(--font-ui)" },
          },
          // ECharts hides a label that will not fit its tile, which is the
          // behaviour we want: the 0.01% bank tile is a sliver, and a name
          // crammed into it would be the only illegible thing on the page.
          overflow: "truncate",
        },
        // No band header on a class that files a split. ECharts reserves the
        // top of the parent tile for it, and at this width "Domestic
        // institutions 13.47%" truncates to nothing useful while eating the
        // room its four parts need. The legend above already maps colour to
        // class, so the parts inherit their parent's hue and are read through
        // it.
        upperLabel: { show: false },
        itemStyle: { borderWidth: 0, borderColor: "transparent", gapWidth: 3 },
        data: nodes,
      }],
    };
  }, [groups]);

  if (!data.available || !groups.length) {
    return <EmptyNote>No shareholding filings available for this company.</EmptyNote>;
  }

  const pledge = data.pledge_pct;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <PanelHead
        title="Shareholding"
        right={
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            {pledge !== null && pledge !== undefined ? (
              <span
                style={{
                  fontSize: 11.5,
                  fontVariantNumeric: "tabular-nums",
                  color: pledge > 0 ? "var(--color-warn)" : "var(--text-secondary)",
                }}
              >
                Promoter pledge <strong style={{ fontWeight: 600 }}>{num(pledge, { dp: 2, pct: true })}</strong>
              </span>
            ) : null}
            {shift ? (
              <span style={{ fontSize: 11.5, color: "var(--text-secondary)", fontVariantNumeric: "tabular-nums" }}>
                <span style={{ color: shift.delta >= 0 ? "var(--color-profit)" : "var(--color-loss)", fontWeight: 600 }}>
                  {shift.delta >= 0 ? "+" : ""}{shift.delta.toFixed(2)} pp
                </span>{" "}
                {shift.name} vs prior quarter
              </span>
            ) : null}
          </div>
        }
      />

      <div
        className="shp-grid"
        style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.75fr) minmax(0, 1fr)", gap: 24 }}
      >
        {/* The chart takes its height from the row, which the readout beside
            it sets. Fixed at 300 it left a half-screen of white under itself
            for any company that files a deep breakdown — the two columns are
            one object, and one of them ending two-thirds early reads as a
            rendering fault rather than as a chart that happens to be short.
            The floor keeps it usable once the columns stack on a phone, where
            the row is no longer sized by anything. */}
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", height: CHART_H }}>
          {option ? (
            <div style={{ flex: 1, minHeight: 0 }}>
              <EChart option={option} height="100%" ariaLabel="Shareholding by owner class over time" />
            </div>
          ) : null}
        </div>

        {/* The latest quarter as area. No border down the left: the treemap is
            already a block with its own edges, and a rule between two charts
            draws a seam where the eye does not need one. */}
        <div
          className="shp-split"
          style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0, height: CHART_H }}
        >
          <div style={{
            fontSize: 11, fontWeight: 650, letterSpacing: "0.08em",
            textTransform: "uppercase", color: "var(--text-primary)",
          }}>
            {data.quarter ? quarterLabel(data.quarter) : "Latest"}
          </div>
          {treemap ? (
            <div style={{ flex: 1, minHeight: 0 }}>
              <EChart option={treemap} height="100%" ariaLabel="Shareholding split this quarter" />
            </div>
          ) : null}
        </div>
      </div>

      {data.holders.length ? <HolderTable holders={data.holders} /> : null}

      <style>{`
        @media (max-width: 720px) {
          .shp-grid { grid-template-columns: 1fr !important; gap: 20px !important; }
          .shp-split { padding-top: 4px; }
        }
      `}</style>
    </div>
  );
}

/** Named holders above the disclosure threshold.
 *
 *  Two columns of rows rather than one long list: at ten-plus holders a single
 *  column pushes the section past a screen for a table nobody scrolls, and the
 *  ranking is still readable read-down-then-across.
 */
function HolderTable({
  holders,
}: {
  holders: ShareholdingResponse["holders"];
}): React.ReactElement {
  const [all, setAll] = React.useState(false);
  const shown = all ? holders : holders.slice(0, 10);

  return (
    <div style={{ marginTop: 4, borderTop: "1px solid var(--glass-border)", paddingTop: 14 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontSize: 10.5, fontWeight: 650, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-tertiary)" }}>
          Named holders
        </span>
        {holders.length > 10 ? (
          <button
            type="button"
            onClick={() => setAll((v) => !v)}
            style={{
              border: "none", background: "transparent", cursor: "pointer",
              fontFamily: "var(--font-ui)", fontSize: 11.5, color: "var(--text-secondary)",
            }}
          >
            {all ? "Show top 10" : `Show all ${holders.length}`}
          </button>
        ) : null}
      </div>

      {/* One line per holder: a name and a number.
       *
       *  It used to be five things — a rule, a name, the holder's class on a
       *  second line, a 46px bar and the percentage — for one fact each, and
       *  four holders cost eight lines of text and four hairlines.
       *
       *  The bar went first: it was scaled against the largest holder, and
       *  with a promoter at 71% beside a fund at 1.3% every other bar was an
       *  empty track. A bar that is always empty is not a reading.
       *
       *  The class line went next. It is real information, so it moves to the
       *  row's title rather than being dropped — but it was a grey second line
       *  under every name, which is the pattern this page is trying not to
       *  have.
       */}
      <div className="shp-holders" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 40 }}>
        {shown.map((h) => (
          <div
            key={h.name}
            title={holderClass(h.bucket) || undefined}
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 14,
              padding: "5px 0",
            }}
          >
            <span
              style={{
                fontSize: 12.5,
                color: "var(--text-primary)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                minWidth: 0,
              }}
            >
              {titleCase(h.name)}
            </span>
            <span style={{
              fontFamily: "var(--font-mono)", fontSize: 12,
              fontWeight: 600, fontVariantNumeric: "tabular-nums",
              color: "var(--text-primary)", whiteSpace: "nowrap",
            }}>
              {num(h.pct, { dp: 2, pct: true })}
            </span>
          </div>
        ))}
      </div>

      <style>{`
        @media (max-width: 720px) {
          .shp-holders { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}

/** Filings name holders in block capitals. Rendered raw they shout down every
 *  other row on the page, so they are cased back — with the initialisms that
 *  genuinely are acronyms left alone. */
const KEEP_CAPS = new Set([
  "ETF", "UTI", "LIC", "NPS", "SBI", "HDFC", "ICICI", "IDFC", "AMC", "A/C",
  "PLC", "LTD", "INC", "NV", "BV", "SA", "AG", "US", "UK", "EM", "MF", "II", "III", "IV",
]);
function titleCase(s: string): string {
  return s
    .toLowerCase()
    .split(/\s+/)
    .map((w) => {
      const bare = w.replace(/[^A-Za-z/]/g, "").toUpperCase();
      if (KEEP_CAPS.has(bare)) return w.toUpperCase();
      return w.replace(/[a-z]/, (c) => c.toUpperCase());
    })
    .join(" ");
}

/** The ownership river.
 *
 *  A streamgraph rather than a stack on a zero baseline. ECharts' `themeRiver`
 *  centres the baseline instead of pinning it to the floor, which is the whole
 *  point: with a flat bottom, every band above the largest one inherits its
 *  wobble, so a promoter block that moves half a point drags four other classes
 *  with it and none of them can be read on their own. Centred, each band
 *  carries only its own change.
 *
 *  The usual objection to streamgraphs — that you cannot read a value off a
 *  floating baseline — mostly does not apply here, because these five classes
 *  sum to exactly 100 by construction. The river's total thickness is constant,
 *  so the shape is pure composition and the axis it would otherwise need is
 *  the one thing it does not miss. The readout beside it carries the numbers.
 *
 *  themeRiver takes one series of [time, value, name] triples rather than a
 *  series per class, and it lays out on a `singleAxis`, not a grid.
 */
function riverOption(
  quarters: ShareholdingResponse["quarters"],
  classes: string[],
): Record<string, unknown> {
  const data: [string, number, string][] = [];
  quarters.forEach((q) => {
    classes.forEach((c) => {
      const v = q[c];
      // Absent rather than zero. A class the company had not begun filing is
      // not a class that owned nothing, and themeRiver reads a zero as a real
      // measurement that pinches the river shut.
      if (typeof v === "number") data.push([q.quarter, v, c]);
    });
  });

  return {
    color: classes.map((c) => OWNER_COLOR[c] ?? "#7A7268"),
    legend: {
      type: "scroll", top: 0, left: 0, itemWidth: 8, itemHeight: 8,
      itemGap: 16, icon: "circle", textStyle: { fontSize: 11 },
      data: classes,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { width: 1, opacity: 0.45 } },
      // themeRiver's own formatter prints the raw date; these are quarters and
      // the reader knows them by label.
      formatter: (params: unknown) => {
        const rows = Array.isArray(params) ? params : [params];
        const first = rows[0] as { value?: [string, number, string] } | undefined;
        const when = first?.value?.[0];
        const head = when ? quarterLabel(String(when).slice(0, 10)) : "";
        const body = rows
          .map((r) => {
            const p = r as { value?: [string, number, string]; color?: string };
            const name = p.value?.[2] ?? "";
            const pct = p.value?.[1];
            return `<div style="display:flex;gap:10px;justify-content:space-between">
              <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${p.color};margin-right:6px"></span>${name}</span>
              <strong>${typeof pct === "number" ? `${pct.toFixed(2)}%` : "—"}</strong></div>`;
          })
          .join("");
        return `<div style="font-weight:600;margin-bottom:3px">${head}</div>${body}`;
      },
    },
    singleAxis: {
      type: "time",
      // The end labels are CENTRED on the first and last points, so half of
      // each hangs outside the axis — at left: 8 the opening quarter read
      // "ep 24". The insets are half a label wide.
      top: 34, bottom: 30, left: 34, right: 34,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: {
        fontSize: 10.5,
        formatter: (v: number) => quarterLabel(new Date(v).toISOString().slice(0, 10)),
      },
      splitLine: { show: false },
    },
    series: [{
      type: "themeRiver",
      // A hairline of the page's ground between bands, the same separator the
      // stacked charts on this page use — without it two adjacent earth tones
      // meet with no edge and the river reads as one mass.
      itemStyle: { borderColor: "#fff", borderWidth: 0.8 },
      emphasis: { focus: "series", itemStyle: { shadowBlur: 0 } },
      // The class names ride in the tooltip and the legend; printed on the
      // bands as well they collide with each other on the thin ones.
      label: { show: false },
      // Nearly none. These five classes sum to 100 by construction, so the
      // river is a constant-thickness band and the gap is pure margin — 8%
      // top and bottom left a fifth of the panel empty for no reading.
      boundaryGap: ["2%", "2%"],
      data,
    }],
  };
}
