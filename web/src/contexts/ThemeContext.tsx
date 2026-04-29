"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type Theme = "dark" | "light";

interface ThemeCtx {
  theme: Theme;
  toggle: () => void;
}

const Ctx = createContext<ThemeCtx>({ theme: "dark", toggle: () => {} });

function applyTheme(t: Theme) {
  const root = document.documentElement;
  root.classList.toggle("dark", t === "dark");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Start with dark — the anti-flash script in layout.tsx already set the
  // correct class on <html> before hydration, so no visible flash occurs.
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    // Sync React state with whatever the anti-flash script applied.
    const stored = localStorage.getItem("cf-theme") as Theme | null;
    const resolved: Theme = stored === "light" ? "light" : "dark";
    setTheme(resolved);
    applyTheme(resolved);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
    localStorage.setItem("cf-theme", next);
  }

  return <Ctx.Provider value={{ theme, toggle }}>{children}</Ctx.Provider>;
}

export function useTheme() {
  return useContext(Ctx);
}
