import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "-apple-system", "sans-serif"],
        mono: ["var(--font-geist-mono)", "SF Mono", "monospace"],
      },
      fontSize: {
        "display": ["3rem", { lineHeight: "1.08", letterSpacing: "-0.025em", fontWeight: "600" }],
        "heading": ["1.5rem", { lineHeight: "1.15", letterSpacing: "-0.02em", fontWeight: "600" }],
        "title": ["1.125rem", { lineHeight: "1.3", letterSpacing: "-0.015em", fontWeight: "600" }],
        "body": ["0.875rem", { lineHeight: "1.5", letterSpacing: "0" }],
        "caption": ["0.75rem", { lineHeight: "1.4", letterSpacing: "0.01em" }],
        "micro": ["0.625rem", { lineHeight: "1.3", letterSpacing: "0.02em" }],
      },
      colors: {
        massclaw: {
          bg: "var(--mc-bg)",
          surface: "var(--mc-surface)",
          border: "var(--mc-border)",
          accent: "var(--mc-accent)",
          "accent-light": "var(--mc-accent-light)",
          blue: "var(--mc-blue)",
          "blue-light": "var(--mc-blue-light)",
          success: "var(--mc-success)",
          warning: "var(--mc-warning)",
          danger: "var(--mc-danger)",
          text: "var(--mc-text)",
          "text-muted": "var(--mc-text-muted)",
        },
      },
      animation: {
        "glow-pulse": "glow-pulse 3s ease-in-out infinite",
        "border-rotate": "border-rotate 4s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
