"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { login } from "@/lib/api";
import { setAuth, useAuth, clearAuth } from "@/lib/auth-store";

export default function LoginPage() {
  const router = useRouter();
  const { user } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: () => login(email.trim(), password),
    onSuccess: (data) => {
      setAuth(data.access_token, data.user);
      const dest = data.user.role === "admin" ? "/marketplace/admin" : "/marketplace/inventor";
      router.push(dest);
    },
  });

  return (
    <div className="max-w-md mx-auto space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Sign in</h1>
        <p className="text-muted-foreground text-sm">
          Inventor or admin account. Used to access the marketplace activation flow.
        </p>
      </div>

      {user && (
        <div className="card space-y-2">
          <div className="text-sm">
            Already signed in as <span className="font-medium">{user.email}</span>{" "}
            <span className="text-xs text-muted-foreground">({user.role})</span>
          </div>
          <button className="btn-secondary" onClick={() => clearAuth()}>
            Sign out
          </button>
        </div>
      )}

      <div className="card space-y-3">
        <input
          className="input"
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
        />
        <input
          className="input"
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
        <button
          className="btn-primary"
          disabled={!email || !password || mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
          Sign in
        </button>
        {mutation.isError && (
          <div className="text-sm text-destructive">
            {(mutation.error as Error)?.message || "Login failed"}
          </div>
        )}
      </div>

      <div className="card border-amber-500/40 bg-amber-500/5 text-xs text-amber-300 leading-relaxed">
        <strong>Phase-1 testing note.</strong> Inventor logins rely on the{" "}
        <code>/marketplace/inventor/claim</code> endpoint, which still has a
        documented impersonation hole — any <code>professor_user</code> account
        can claim any faculty profile until the verified-claim flow lands. Use
        only with controlled test accounts. See BUILD_SUMMARY.md.
      </div>
    </div>
  );
}
