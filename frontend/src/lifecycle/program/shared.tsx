/** What every program section receives, and the small header chip the accordion shows for each. */
import { ReactNode } from "react";
import type { Program } from "../types";

export type Relation = "hire" | "mentor" | "manager";
export type SectionProps = {
  id: string; p: Program; rel: Relation; reload: () => void;
  who: (userId: string | null | undefined) => string; // a member's name by id ("Priya")
  meId: string;
};

export function Meta({ children, tone }: { children: ReactNode; tone?: "attn" | "late" }) {
  return <span className={`lc-meta${tone ? ` ${tone}` : ""}`}>{children}</span>;
}

/** A row of small actions under or beside an item. */
export function Actions({ children }: { children: ReactNode }) {
  return <div className="lc-actions">{children}</div>;
}
