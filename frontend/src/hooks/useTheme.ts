import { create } from "zustand";

type Theme = "dark" | "light";

interface ThemeStore {
  theme: Theme;
  toggle: () => void;
  set: (theme: Theme) => void;
}

export const useTheme = create<ThemeStore>((set) => ({
  theme: "dark",
  toggle: () =>
    set((state) => {
      const next = state.theme === "dark" ? "light" : "dark";
      if (typeof document !== "undefined") {
        document.documentElement.classList.remove("dark", "light");
        document.documentElement.classList.add(next);
      }
      return { theme: next };
    }),
  set: (theme) => {
    if (typeof document !== "undefined") {
      document.documentElement.classList.remove("dark", "light");
      document.documentElement.classList.add(theme);
    }
    set({ theme });
  },
}));
