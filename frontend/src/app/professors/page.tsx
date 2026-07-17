"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getProfessors } from "@/lib/api";
import { GridSkeleton } from "@/components/skeleton";

export default function ProfessorsPage() {
  const [dept, setDept] = useState("");
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["professors", dept],
    queryFn: () => getProfessors(dept || undefined, 600),
  });

  const list = (data?.professors ?? []).filter((p) =>
    !q ||
    p.name.toLowerCase().includes(q.toLowerCase()) ||
    p.research_areas.some((r) => r.toLowerCase().includes(q.toLowerCase())),
  );

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Professor directory</h1>
        <p className="text-muted-foreground">{data?.count ?? 0} professors indexed</p>
      </div>
      <div className="flex gap-3 flex-wrap">
        <input
          className="input max-w-sm" placeholder="Search by name or research area"
          value={q} onChange={(e) => setQ(e.target.value)}
        />
        <input
          className="input max-w-xs" placeholder="Filter by department"
          value={dept} onChange={(e) => setDept(e.target.value)}
        />
      </div>
      {isLoading ? (
        <GridSkeleton count={9} />
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {list.map((p) => (
            <Link
              key={p.professor_id}
              href={`/professors/${p.professor_id}`}
              className="card hover:border-primary/50 transition"
            >
              <div className="font-medium">{p.name}</div>
              <div className="text-xs text-muted-foreground">{p.department}</div>
              {p.designation && (
                <div className="text-xs text-muted-foreground mt-1">{p.designation}</div>
              )}
              <div className="mt-3 flex flex-wrap gap-1">
                {p.research_areas.slice(0, 3).map((r) => (
                  <span key={r} className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
                    {r}
                  </span>
                ))}
              </div>
              {p.patent_count ? (
                <div className="mt-2 text-xs text-accent">{p.patent_count} patents on file</div>
              ) : null}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
