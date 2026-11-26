/** Identity Platform (Firebase Auth). Only ever loaded with a dynamic import, so the demo bundle does not carry it. */
import { getApps, initializeApp } from "firebase/app";
import {
  connectAuthEmulator, createUserWithEmailAndPassword, getAuth, GoogleAuthProvider, onIdTokenChanged, sendEmailVerification,
  sendPasswordResetEmail, signInWithEmailAndPassword, signInWithPopup, signOut, type Auth, type User,
} from "firebase/auth";

let auth: Auth;

export function init(options: { apiKey: string; authDomain: string; projectId: string; emulator?: string | null }) {
  const fresh = !getApps().length;
  auth = getAuth(getApps()[0] ?? initializeApp({ apiKey: options.apiKey, authDomain: options.authDomain, projectId: options.projectId }));
  // development only (the server refuses it in production): accounts live in the local Firebase Auth emulator
  if (options.emulator && fresh) connectAuthEmulator(auth, options.emulator, { disableWarnings: true });
  auth.useDeviceLanguage();
}

export const onUser = (cb: (user: User | null) => void) => onIdTokenChanged(auth, cb);
/** Cached until about five minutes before it expires; `force` always fetches a new one. */
export const token = async (force: boolean) => (auth.currentUser ? auth.currentUser.getIdToken(force) : null);
export const email = () => auth.currentUser?.email ?? "";
export const displayName = () => auth.currentUser?.displayName ?? "";

export function google() {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" }); // let people pick the address they were invited with
  return signInWithPopup(auth, provider);
}
export const signIn = (address: string, password: string) => signInWithEmailAndPassword(auth, address, password);
export async function createAccount(address: string, password: string) {
  const cred = await createUserWithEmailAndPassword(auth, address, password);
  await sendEmailVerification(cred.user);
}
export const resetPassword = (address: string) => sendPasswordResetEmail(auth, address);
export async function resendVerification() {
  if (auth.currentUser) await sendEmailVerification(auth.currentUser);
}
/** Re-reads the account and gets a token that carries the new email_verified claim. */
export async function refreshVerification() {
  const u = auth.currentUser;
  if (!u) return false;
  await u.reload();
  await u.getIdToken(true);
  return u.emailVerified;
}
export const logOut = () => signOut(auth);
