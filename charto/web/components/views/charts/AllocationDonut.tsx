"use client";

/**
 * AllocationDonut — basket weights as a monochrome donut (HEADLINE viz for
 * basket expressions).
 *
 * Monochrome ramp (--text-primary at decreasing opacity). The largest slice
 * turns --pivot-blue ONLY when it exceeds `cap` (a single-name cap-breach
 * signal). Center shows the top holding + its weight; a legend column lists
 * SYM nn% with rounded swatches.
 *
 * DESIGN LAW: rounded corners, borders-only empty state, every label >= 13px,
 * Inter-tabular numerals. The "basket purity" 0-1 token is NOT rendered (raw
 * 0-1 internals are banned on screen); `purity` is accepted only to keep the
 * call site stable.
 */

import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import { useTokenColors } from "../use-token-color";
import { Num, Stat } from "../Stat";

const RAMP = [1, 0.78, 0.6, 0.46, 0.34, 0.24];

export function AllocationDonut({
  weights,
  cap,
  height = 160,
}: {
  weights: Record<string, number>;
  cap?: number;
  /** Accepted for call-site stability; never rendered (banned raw 0-1 token). */
  purity?: number;
  height?: number;
}): React.ReactElement {
  const c = useTokenColors({
    ink: "--text-primary",
    blue: "--pivot-blue",
  });

  const entries = Object.entries(weights)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a);

  if (entries.length === 0) {
    return (
      <div
        className="rounded-lg"
        style={{
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--glass-border)",
          background: "var(--bg-base)",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 15,
            color: "var(--text-tertiary)",
          }}
        >
          Equal-weight basket
        </span>
      </div>
    );
  }

  const top = entries[0]!;
  const capBreached = cap !== undefined && entries.some(([, v]) => v > cap);
  const topBreaches = cap !== undefined && top[1] > cap;

  const data = entries.map(([sym, frac], i) => ({
    sym,
    frac,
    fill: i === 0 && topBreaches ? c.blue : c.ink,
    opacity: i === 0 && topBreaches ? 1 : RAMP[Math.min(i, RAMP.length - 1)],
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        {/* Donut + center label */}
        <div style={{ position: "relative", width: height, height, flexShrink: 0 }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="frac"
                nameKey="sym"
                innerRadius={height * 0.3}
                outerRadius={height * 0.44}
                paddingAngle={2}
                strokeWidth={0}
                isAnimationActive={false}
              >
                {data.map((d) => (
                  <Cell key={d.sym} fill={d.fill} fillOpacity={d.opacity} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              pointerEvents: "none",
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-secondary)",
                letterSpacing: "-0.01em",
              }}
            >
              {top[0]}
            </span>
            <Num size="value" color="var(--text-primary)">
              {(top[1] * 100).toFixed(0)}%
            </Num>
          </div>
        </div>

        {/* Legend column */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            minWidth: 0,
            flex: 1,
          }}
        >
          {entries.slice(0, 6).map(([sym, frac], i) => (
            <div
              key={sym}
              style={{ display: "flex", alignItems: "center", gap: 8 }}
            >
              <span
                aria-hidden
                className="rounded-sm"
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: "var(--radius-xs)",
                  flexShrink: 0,
                  background: i === 0 && topBreaches ? c.blue : c.ink,
                  opacity:
                    i === 0 && topBreaches
                      ? 1
                      : RAMP[Math.min(i, RAMP.length - 1)],
                }}
              />
              <span
                style={{
                  fontFamily: "var(--font-display)",
                  fontSize: 13,
                  color: "var(--text-secondary)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {sym}
              </span>
              <Num
                size="md"
                color="var(--text-primary)"
                style={{ marginLeft: "auto" }}
              >
                {(frac * 100).toFixed(0)}%
              </Num>
            </div>
          ))}
          {entries.length > 6 && (
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                color: "var(--text-tertiary)",
              }}
            >
              +{entries.length - 6} more
            </span>
          )}
        </div>
      </div>

      {cap !== undefined && (
        <Stat
          label="Single-name cap"
          value={`${(cap * 100).toFixed(0)}%`}
          valueColor={capBreached ? "var(--color-warn)" : "var(--text-primary)"}
          valueSize="value"
          sub={capBreached ? "breached" : undefined}
        />
      )}
    </div>
  );
}
