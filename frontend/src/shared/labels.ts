import type { Department, FunctionalRole } from "../types";

/**
 * Display strings live apart from the data. `mockData.ts` holds what the
 * backend will send (`"domain_expert"`); these are what a person reads
 * ("Domain expert"). Keeping them separate means a wording change never
 * touches the data contract.
 */

export const DEPARTMENT_LABEL: Record<Department, string> = {
  engineering: "Engineering",
  sales: "Sales",
  hr: "HR",
};

export const ROLE_LABEL: Record<FunctionalRole, string> = {
  new_hire: "New hire",
  domain_expert: "Domain expert",
  manager: "Manager",
  admin: "Admin",
};

/** "Aman Nautiyal" → "AN", for avatar chips. */
export function initialsOf(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

/** "2026-09-22T13:00:00Z" → "22 Sep 2026". Empty string for missing dates. */
export function formatDate(iso: string | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
