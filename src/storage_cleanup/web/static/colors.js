// Fixed color slots: a category (or location) keeps its color in every chart,
// whatever its rank. Slots come from the dataviz reference palette.
import { token } from "./format.js";

const CATEGORY_SLOTS = {
  "ai-tooling": "--series-1",
  applications: "--series-2",
  downloads: "--series-3",
  projects: "--series-4",
  docker: "--series-5",
  "dev-caches": "--series-6",
  homebrew: "--series-7",
  "app-data": "--series-8",
  home: "--neutral-1",
  packages: "--neutral-3",
  "leftover-app-data": "--neutral-3",
  "system-other": "--neutral-2",
};

const LOCATION_SLOTS = {
  Workspace: "--series-1",
  Applications: "--series-2",
  Downloads: "--series-3",
  "Your personal data": "--series-4",
  "Docker virtual disk": "--series-5",
  Desktop: "--series-6",
  Homebrew: "--series-7",
  Library: "--series-8",
  "system-other": "--neutral-2",
};

// Fills that need dark label ink (light fills); everything else takes white.
const DARK_INK = new Set(["--series-4", "--neutral-1", "--neutral-2", "--neutral-3"]);

export function slotFor(lens, group) {
  const slots = lens === "locations" ? LOCATION_SLOTS : CATEGORY_SLOTS;
  return slots[group] ?? "--neutral-1";
}

export function fillFor(lens, group) {
  return token(slotFor(lens, group));
}

function isDark() {
  const theme = document.documentElement.dataset.theme;
  if (theme) return theme === "dark";
  return matchMedia("(prefers-color-scheme: dark)").matches;
}

// Palette rule: labels inside fills are white, except on light fills in light
// mode. Every dark-mode fill takes white.
export function inkFor(lens, group) {
  if (isDark()) return "#ffffff";
  return DARK_INK.has(slotFor(lens, group)) ? "#0b0b0b" : "#ffffff";
}
