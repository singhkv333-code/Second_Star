/**
 * The shell's own images, bundled rather than addressed by a root path.
 *
 * In production nginx sends only the shell's pages and `/_next/` to this app;
 * every other root path falls through to charto's data server. A file in
 * `public/` addressed as `/loaders/x.svg` therefore answered the data
 * server's 404 (or, under `/brokers/`, its broker API's 401), and the loader
 * rendered as a broken image reading "Loading". A bundled asset is emitted
 * under `/_next/static/media/` with a content hash, which the shell always
 * serves and the browser can cache for good.
 *
 * `new URL(…, import.meta.url)` rather than `import`: Next's image loader
 * skips URL dependencies, so these go through webpack's plain asset handling.
 * An `import` of a PNG makes Next draw a blur placeholder with sharp, whose
 * native binary the VM's `pnpm install --ignore-scripts` never fetches — the
 * production build failed on exactly that. `pathname` drops the origin, so
 * the server render and the browser agree on the same `/_next/…` path.
 */
const asset = (u: URL): string => u.pathname;

export const LOADER_CUBES = asset(new URL("../public/loaders/isometric-cubes.svg", import.meta.url));
export const CHARTO_MARK = asset(new URL("../public/charto-mark.png", import.meta.url));
export const PIVOT_ICON = asset(new URL("../public/pivot-icon.png", import.meta.url));
export const PIVOT_LIGHT = asset(new URL("../public/pivot-light.png", import.meta.url));

const BROKER_LOGOS: Record<string, string> = {
  angelone: asset(new URL("../public/brokers/angelone.svg", import.meta.url)),
  dhan: asset(new URL("../public/brokers/dhan.svg", import.meta.url)),
  fyers: asset(new URL("../public/brokers/fyers.svg", import.meta.url)),
  groww: asset(new URL("../public/brokers/groww.svg", import.meta.url)),
  kite: asset(new URL("../public/brokers/kite.svg", import.meta.url)),
  upstox: asset(new URL("../public/brokers/upstox.svg", import.meta.url)),
  zerodha: asset(new URL("../public/brokers/zerodha.svg", import.meta.url)),
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
