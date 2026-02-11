"use client";

import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getEffectiveTheme, getStoredTheme, setStoredTheme, applyTheme, type Theme } from "@/lib/theme-utils";

export function ThemeToggle() {
  function toggleTheme() {
    const stored = getStoredTheme();
    const effective = getEffectiveTheme(stored);
    const newTheme: Theme = effective === "dark" ? "light" : "dark";
    setStoredTheme(newTheme);
    applyTheme(newTheme);
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={toggleTheme}
      className="w-8 h-8 p-0"
      title="切换明暗模式"
    >
      <Sun className="h-4 w-4 dark:hidden" />
      <Moon className="h-4 w-4 hidden dark:block" />
    </Button>
  );
}
