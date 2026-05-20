import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadSerif } from "@remotion/google-fonts/InstrumentSerif";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

const inter = loadInter("normal", {
  weights: ["300", "400", "500", "600"],
  subsets: ["latin"],
});

// Instrument Serif has only weight "400" — load both normal + italic styles.
const serifRegular = loadSerif("normal", {
  weights: ["400"],
  subsets: ["latin"],
});
const serifItalic = loadSerif("italic", {
  weights: ["400"],
  subsets: ["latin"],
});

const mono = loadMono("normal", {
  weights: ["400", "500"],
  subsets: ["latin"],
});

export const fontSans = inter.fontFamily;
export const fontSerif = serifRegular.fontFamily;
export const fontSerifItalic = serifItalic.fontFamily;
export const fontMono = mono.fontFamily;
