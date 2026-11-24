/**
 * Shared look of the downloadable PDFs: the dark "research terminal" palette of the
 * product pages (globals.css --ib-* tokens, flattened onto the page background, since a
 * PDF has no translucency over a body colour) and the Inter typeface served from
 * /fonts/inter (OFL-1.1, Latin + Latin Extended subset so European names render).
 */

import { Font, StyleSheet } from "@react-pdf/renderer";

export const C = {
  page: "#060913",
  surface: "#0d121c",
  surfaceRaised: "#131a26",
  line: "#1d2533",
  lineStrong: "#2c3646",
  ink: "#f1f5f9",
  ink2: "#b6c2d3",
  ink3: "#7e8ca0",
  accent: "#7cc7f5",
  accentQuiet: "#13283a",
  positive: "#5fd3a1",
  negative: "#f08a7e",
  warning: "#f2c46d",
} as const;

let registered = false;

/** Registers Inter once per page load; react-pdf fetches the files on first render. */
export function registerFonts(origin: string): void {
  if (registered) return;
  const src = (w: string) => `${origin}/fonts/inter/Inter-${w}.ttf`;
  Font.register({
    family: "Inter",
    fonts: [
      { src: src("Regular"), fontWeight: 400 },
      { src: src("Medium"), fontWeight: 500 },
      { src: src("SemiBold"), fontWeight: 600 },
      { src: src("Bold"), fontWeight: 700 },
    ],
  });
  // Words are never split with a hyphen: tickers and figures must stay whole.
  Font.registerHyphenationCallback((word) => [word]);
  registered = true;
}

export const s = StyleSheet.create({
  page: {
    backgroundColor: C.page,
    color: C.ink2,
    fontFamily: "Inter",
    fontSize: 8.5,
    lineHeight: 1.45,
    paddingTop: 34,
    paddingBottom: 46,
    paddingHorizontal: 36,
  },
  eyebrow: { fontSize: 7, letterSpacing: 1.4, color: C.ink3, textTransform: "uppercase" },
  title: { fontSize: 20, fontWeight: 600, color: C.ink, marginTop: 4, lineHeight: 1.25 },
  subtitle: { fontSize: 9, color: C.ink3, marginTop: 3 },
  h2: {
    fontSize: 7.5,
    fontWeight: 600,
    letterSpacing: 1.3,
    textTransform: "uppercase",
    color: C.ink3,
    marginTop: 14,
    marginBottom: 6,
  },
  card: {
    backgroundColor: C.surface,
    borderColor: C.line,
    borderWidth: 0.75,
    borderRadius: 6,
    padding: 10,
  },
  row: { flexDirection: "row" },
  small: { fontSize: 7, color: C.ink3 },
  body: { fontSize: 8.5, color: C.ink2 },
  strong: { color: C.ink, fontWeight: 600 },
  footer: {
    position: "absolute",
    left: 36,
    right: 36,
    bottom: 18,
    flexDirection: "row",
    justifyContent: "space-between",
    fontSize: 6.5,
    color: C.ink3,
    borderTopColor: C.line,
    borderTopWidth: 0.75,
    paddingTop: 6,
  },
});
