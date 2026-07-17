"use client";

import { useEffect, useState } from "react";
import type { AuthUser } from "./api";

const TOKEN_KEY = "collabv_token";
const USER_KEY = "collabv_user";

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function setAuth(token: string, user: AuthUser) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  window.dispatchEvent(new Event("collabv-auth-change"));
}

export function clearAuth() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event("collabv-auth-change"));
}

// React hook so components re-render on login/logout in this tab.
export function useAuth(): { user: AuthUser | null; token: string | null } {
  const [state, setState] = useState<{ user: AuthUser | null; token: string | null }>(
    { user: null, token: null },
  );
  useEffect(() => {
    const sync = () => setState({ user: getStoredUser(), token: getStoredToken() });
    sync();
    window.addEventListener("collabv-auth-change", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("collabv-auth-change", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return state;
}
