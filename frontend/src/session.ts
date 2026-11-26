import { createContext, useContext } from "react";
import type { Config, Me, Project } from "./api";

/** Who is signed in and which project they are looking at. Every page reads the project from here. */
export type Session = {
  config: Config; me: Me; project: Project; demo: boolean;
  reloadAccount: (() => void) | null; // production only: resolve the login again (after leaving the organisation)
  signOut: (() => void) | null; // end this session (after deleting the organisation)
};

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession must be used inside a project");
  return s;
}

