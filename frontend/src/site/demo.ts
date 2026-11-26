/* Shared shapes the site draws from the demo project's seeded KT call (backend/app/seed.py, "KT #1: How validation works"):
   the call's waveform (About's fading call) and the intro's wave (the finale's contours). */

/** The KT call, 0-105 s of its 270, and when Ravi is speaking. */
const CALL_SECONDS = 105;
const SPEECH: [number, number][] = [[4, 18], [20, 52], [55, 95], [98, 105]];

/** One bar per second: speech at 25-100 % height (seeded, so every visit draws the same call), silence at 6 %. */
export function waveform(): number[] {
  let a = 7;
  const rand = () => { // mulberry32
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return Array.from({ length: CALL_SECONDS }, (_, s) => {
    const r = rand();
    return SPEECH.some(([from, to]) => s >= from && s <= to) ? 0.25 + 0.75 * r : 0.06;
  });
}

/** The intro's narrow wave (index.html, .ww-waves--narrow), x -210..10, resting near y 57.6: the line every curve here borrows. */
export const SWELL: [number, number][] = (
  "-210 57.6 -207.5 57.5 -205 57.5 -202.5 57.5 -200 57.5 -197.5 57.5 -195 57.6 -192.5 57.8 -190 57.9 -187.5 58.1 -185 58.3 " +
  "-182.5 58.6 -180 58.9 -177.5 59.2 -175 59.5 -172.5 59.9 -170 60.2 -167.5 60.5 -165 60.9 -162.5 61.1 -160 61.4 -157.5 61.6 " +
  "-155 61.7 -152.5 61.7 -150 61.7 -147.5 61.6 -145 61.4 -142.5 61 -140 60.6 -137.5 60.1 -135 59.6 -132.5 58.9 -130 58.2 " +
  "-127.5 57.4 -125 56.7 -122.5 55.9 -120 55.1 -117.5 54.4 -115 53.8 -112.5 53.3 -110 52.9 -107.5 52.7 -105 52.7 -102.5 52.8 " +
  "-100 53.1 -97.5 53.6 -95 54.3 -92.5 55.2 -90 56.3 -87.5 57.5 -85 58.9 -82.5 60.4 -80 61.9 -77.5 63.4 -75 64.9 -72.5 66.3 " +
  "-70 67.6 -67.5 68.7 -65 69.6 -62.5 70.2 -60 70.5 -57.5 70.6 -55 70.2 -52.5 69.6 -50 68.5 -47.5 67.2 -45 65.5 -42.5 63.5 " +
  "-40 61.3 -37.5 58.9 -35 56.4 -32.5 53.8 -30 51.2 -27.5 48.7 -25 46.4 -22.5 44.3 -20 42.5 -17.5 41.1 -15 40.2 -12.5 39.7 " +
  "-10 39.8 -7.5 40.5 -5 41.6 -2.5 43.4 0 45.7 2.5 48.4 5 51.6 7.5 55.1 10 58.9"
).split(" ").reduce<[number, number][]>((pts, v, i, all) => (i % 2 ? pts : [...pts, [+v, +all[i + 1]]]), []);
