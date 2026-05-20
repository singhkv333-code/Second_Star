// Centralized font loading so every scene resolves the same family.
// Mirrors pivot-next's globals.css:
//   --font-display / --font-ui   → Inter
//   --font-experiment / --serif  → Newsreader
//   --font-mono                  → JetBrains Mono

import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadNewsreader } from "@remotion/google-fonts/Newsreader";
import { loadFont as loadJetBrains } from "@remotion/google-fonts/JetBrainsMono";

const inter = loadInter("normal", {
  weights: ["400", "500", "600", "700"],
  subsets: ["latin"],
});
const newsreader = loadNewsreader("normal", {
  weights: ["400", "500", "600", "700"],
  subsets: ["latin"],
});
const mono = loadJetBrains("normal", {
  weights: ["400", "500", "600"],
  subsets: ["latin"],
});

export const fontUi = inter.fontFamily;
export const fontDisplay = inter.fontFamily;
export const fontSerif = newsreader.fontFamily;
export const fontMono = mono.fontFamily;
