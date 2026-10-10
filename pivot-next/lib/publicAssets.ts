/**
 * The shell's own images, imported rather than addressed by a root path.
 *
 * In production nginx sends only the shell's pages and `/_next/` to this app;
 * every other root path falls through to charto's data server. A file in
 * `public/` addressed as `/loaders/x.svg` therefore answered the data
 * server's 404 (or, under `/brokers/`, its broker API's 401), and the loader
 * rendered as a broken image reading "Loading". An imported asset is emitted
 * under `/_next/static/media/` with a content hash, which the shell always
 * serves and the browser can cache for good.
 */
import isometricCubes from "@/public/loaders/isometric-cubes.svg";
import chartoMark from "@/public/charto-mark.png";
import pivotIcon from "@/public/pivot-icon.png";
import pivotLight from "@/public/pivot-light.png";
import angelone from "@/public/brokers/angelone.svg";
import dhan from "@/public/brokers/dhan.svg";
import fyers from "@/public/brokers/fyers.svg";
import groww from "@/public/brokers/groww.svg";
import kite from "@/public/brokers/kite.svg";
import upstox from "@/public/brokers/upstox.svg";
import zerodha from "@/public/brokers/zerodha.svg";

type Imported = string | { src: string };
const url = (a: Imported): string => (typeof a === "string" ? a : a.src);

export const LOADER_CUBES = url(isometricCubes);
export const CHARTO_MARK = url(chartoMark);
export const PIVOT_ICON = url(pivotIcon);
export const PIVOT_LIGHT = url(pivotLight);

const BROKER_LOGOS: Record<string, string> = {
  angelone: url(angelone),
  dhan: url(dhan),
  fyers: url(fyers),
  groww: url(groww),
  kite: url(kite),
  upstox: url(upstox),
  zerodha: url(zerodha),
};

/** A broker's logo. The server names its own as `/brokers/<id>.svg`, a path
 *  that collides with the broker API in production, so that form and the
 *  bare id both resolve to the bundled file; any other URL passes through. */
export function brokerLogo(id: string, given?: string | null): string | undefined {
  const named = given?.match(/^\/brokers\/([\w-]+)\.svg$/)?.[1];
  const key = (named || (!given ? id : "")).toLowerCase();
  if (key && BROKER_LOGOS[key]) return BROKER_LOGOS[key];
  return given || undefined;
}
