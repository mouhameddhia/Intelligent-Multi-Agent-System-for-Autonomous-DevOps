/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      keyframes: {
        dash: { to: { strokeDashoffset: "-20" } },
        glow: { "0%,100%": { opacity: "0.35" }, "50%": { opacity: "1" } },
      },
      animation: { dash: "dash 0.8s linear infinite", glow: "glow 1.4s ease-in-out infinite" },
    },
  },
  plugins: [],
};
