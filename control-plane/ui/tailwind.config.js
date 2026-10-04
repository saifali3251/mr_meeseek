/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        meeseek: {
          950: "#060911",
          900: "#0a0f1d",
          850: "#0f1629",
          800: "#151e36",
          700: "#1e2b4c",
          600: "#2d3e68",
          border: "#1e293b",
          borderLight: "#334155",
        },
        brand: {
          cyan: "#00d2ff",
          cyanGlow: "rgba(0, 210, 255, 0.2)",
          emerald: "#10b981",
          purple: "#a855f7",
          amber: "#f59e0b",
          red: "#ef4444",
        },
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "Courier New", "monospace"],
      },
      animation: {
        "pulse-subtle": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "glow": "glow 2s ease-in-out infinite alternate",
      },
      keyframes: {
        glow: {
          "0%": { boxShadow: "0 0 10px rgba(0, 210, 255, 0.2)" },
          "100%": { boxShadow: "0 0 20px rgba(0, 210, 255, 0.5)" },
        },
      },
    },
  },
  plugins: [],
};

