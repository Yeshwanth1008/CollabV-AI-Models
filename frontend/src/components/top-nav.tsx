"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut } from "lucide-react";
import { cn } from "@/lib/utils";
import { clearAuth, useAuth } from "@/lib/auth-store";

const BASE_NAV = [
  { href: "/", label: "Home" },
  { href: "/match", label: "Match" },
  { href: "/professor-match", label: "Prof Match" },
  { href: "/marketplace/browse", label: "Browse patents" },
  { href: "/professor-dashboard", label: "Professor Dashboard" },
  { href: "/company-dashboard", label: "Company Dashboard" },
  { href: "/student-dashboard", label: "Student Dashboard" },
  { href: "/employee-dashboard", label: "Employee Dashboard" },
  { href: "/professors", label: "Professors" },
];

export function TopNav() {
  const pathname = usePathname();
  const { user } = useAuth();

  const nav = [...BASE_NAV];
  if (user) {
    if (user.role === "admin") {
      nav.push({ href: "/marketplace/admin", label: "Admin queue" });
    }
    nav.push({ href: "/marketplace/inventor", label: "My listings" });
    nav.push({ href: "/marketplace/buyer/recommendations", label: "Recommended" });
    nav.push({ href: "/marketplace/buyer/inbox", label: "Inbox" });
  }

  return (
    <header className="border-b border-border bg-card/40 backdrop-blur sticky top-0 z-20">
      <div className="container mx-auto flex items-center justify-between px-4 py-3">
        <Link href="/" className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-accent" />
          <span className="text-lg font-semibold tracking-tight">CollabV AI</span>
        </Link>
        <nav className="flex gap-1 items-center">
          {nav.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={cn(
                "rounded-md px-3 py-2 text-sm transition",
                pathname === n.href || pathname.startsWith(n.href + "/")
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted",
              )}
            >
              {n.label}
            </Link>
          ))}
          {user && (
            <button
              onClick={() => clearAuth()}
              className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-muted flex items-center gap-1"
              title={`Signed in as ${user.email} (${user.role})`}
            >
              <LogOut className="h-3.5 w-3.5" /> Sign out
            </button>
          )}
        </nav>
      </div>
    </header>
  );
}
