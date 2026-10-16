"use client";

/**
 * One look per sector: an icon and a hue, used wherever a sector is drawn
 * (the moves list, the rotation map, the quadrant list) so a sector is the
 * same thing to the eye in each of them. The hues are spread round the wheel
 * and drawn soft (see `.sector-chip` and `.sector-dot` in globals.css): they
 * tell sectors apart, they do not say up or down, which stays green and red.
 */

import {
  BrickWall, Building2, Car, Clapperboard, Cog, Construction, Cpu,
  FlaskConical, Fuel, HandCoins, Landmark, Pickaxe, Pill, RadioTower, Shapes,
  Shirt, ShoppingBasket, Sofa, Store, Truck, Zap,
  type LucideIcon,
} from "lucide-react";

const LOOK: Record<string, { icon: LucideIcon; hue: number }> = {
  banks: { icon: Landmark, hue: 215 },
  financials: { icon: HandCoins, hue: 195 },
  it: { icon: Cpu, hue: 250 },
  pharma: { icon: Pill, hue: 160 },
  fmcg: { icon: ShoppingBasket, hue: 30 },
  auto: { icon: Car, hue: 5 },
  energy: { icon: Fuel, hue: 20 },
  power: { icon: Zap, hue: 48 },
  metals: { icon: Pickaxe, hue: 200 },
  materials: { icon: BrickWall, hue: 15 },
  chemicals: { icon: FlaskConical, hue: 280 },
  capital_goods: { icon: Cog, hue: 225 },
  infra: { icon: Construction, hue: 38 },
  realty: { icon: Building2, hue: 340 },
  telecom: { icon: RadioTower, hue: 265 },
  media: { icon: Clapperboard, hue: 320 },
  consumer_durables: { icon: Sofa, hue: 175 },
  consumer_services: { icon: Store, hue: 140 },
  logistics: { icon: Truck, hue: 185 },
  textiles: { icon: Shirt, hue: 300 },
  diversified: { icon: Shapes, hue: 60 },
};

const NEUTRAL = { icon: Shapes, hue: 220 };

export function sectorLook(key: string): { icon: LucideIcon; hue: number } {
  return LOOK[key] ?? NEUTRAL;
}

/** The sector's icon on a soft tile of its hue. */
export function SectorIcon({ sector, size = 22 }: { sector: string; size?: number }): React.ReactElement {
  const { icon: Icon, hue } = sectorLook(sector);
  return (
    <span
      className="sector-chip inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.28), ["--sh" as string]: hue }}
      aria-hidden
    >
      <Icon size={Math.round(size * 0.6)} strokeWidth={1.9} />
    </span>
  );
}
