import { useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";
export const THEME_KEY = "tombaugh-theme";

function stored(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

/** Applies a theme to the page: "system" follows the device; light and dark override it. */
export function applyTheme(t: Theme) {
  const root = document.documentElement;
  if (t === "system") delete root.dataset.theme;
  else root.dataset.theme = t;
  try {
    if (t === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, t);
  } catch {
    // storage blocked (private window): the choice lasts for this visit only
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(stored);
  useEffect(() => { applyTheme(theme); }, [theme]);
  const options: { id: Theme; label: string }[] = [
    { id: "system", label: "System" },
    { id: "light", label: "Light" },
    { id: "dark", label: "Dark" },
  ];
  return (
    <div role="radiogroup" aria-label="Colour theme" className="flex shrink-0 text-xs">
      {options.map((o, i) => (
        <button key={o.id} type="button" role="radio" aria-checked={theme === o.id} onClick={() => setTheme(o.id)}
          className={`border px-2 py-0.5 ${i > 0 ? "-ml-px" : "rounded-l-sm"} ${i === options.length - 1 ? "rounded-r-sm" : ""} ${
            theme === o.id ? "relative border-ink bg-ink text-paper" : "border-rule-strong bg-panel text-muted hover:text-ink"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
