import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        massclaw: {
          bg: "#0a0a0f",
          surface: "#12121a",
          border: "#1e1e2e",
          accent: "#6366f1",
          "accent-light": "#818cf8",
          success: "#22c55e",
          warning: "#f59e0b",
          danger: "#ef4444",
          text: "#e2e8f0",
          "text-muted": "#94a3b8",
        },
      },
    },
  },
  plugins: [],
};

export default config;
