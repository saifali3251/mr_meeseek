/** @type {import('tailwindcss').Config} */
// Every colour is a CSS variable (see src/index.css) so the whole console
// re-themes between the Meeseek light ("day") and dark ("night") palettes,
// matching the landing page. Values are "R G B" triplets so Tailwind's
// opacity modifiers (bg-cyan-500/10 etc.) keep working.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (prefix, steps) =>
  Object.fromEntries(steps.map((s) => [s, v(`${prefix}-${s}`)]));
const STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        white: v("white"),
        meeseek: {
          950: v("m-950"),
          900: v("m-900"),
          850: v("m-850"),
          800: v("m-800"),
          700: v("m-700"),
          600: v("m-600"),
          border: v("m-border"),
          borderLight: v("m-border-light"),
        },
        slate: scale("slate", STEPS),
        cyan: scale("cyan", STEPS),
        blue: scale("blue", STEPS),
        emerald: scale("emerald", STEPS),
        amber: scale("amber", STEPS),
        red: scale("red", STEPS),
        purple: scale("purple", STEPS),
        brand: {
          cyan: v("cyan-400"),
          cyanGlow: "rgb(var(--cyan-400) / 0.2)",
          emerald: v("emerald-400"),
          purple: v("purple-400"),
          amber: v("amber-400"),
          red: v("red-400"),
        },
      },
      fontFamily: {
        sans: ["Manrope", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        display: ["Plus Jakarta Sans", "Manrope", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "Courier New", "monospace"],
      },
      animation: {
        "pulse-subtle": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        glow: "glow 2s ease-in-out infinite alternate",
        fadeIn: "fadeIn .45s cubic-bezier(.2,.7,.2,1) both",
      },
      keyframes: {
        glow: {
          "0%": { boxShadow: "0 0 10px rgb(var(--cyan-400) / 0.2)" },
          "100%": { boxShadow: "0 0 20px rgb(var(--cyan-400) / 0.5)" },
        },
        fadeIn: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "none" },
        },
      },
    },
  },
  plugins: [],
};
