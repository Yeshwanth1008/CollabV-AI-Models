"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Save } from "lucide-react";
import {
  createBuyerProfile,
  getMyBuyerProfile,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-store";

export default function BuyerProfilePage() {
  const router = useRouter();
  const { user } = useAuth();

  const existing = useQuery({
    queryKey: ["my-buyer-profile"],
    queryFn: getMyBuyerProfile,
    enabled: !!user,
    retry: false,
  });

  const [form, setForm] = useState({
    org_name: "",
    industry: "",
    technical_areas: "",  // comma-separated input
    use_cases: "",
    budget_band: "medium",
    tech_maturity_preference: "mid_stage",
  });

  useEffect(() => {
    if (existing.data) {
      setForm({
        org_name: existing.data.org_name || "",
        industry: existing.data.industry || "",
        technical_areas: (existing.data.technical_areas || []).join(", "),
        use_cases: existing.data.use_cases || "",
        budget_band: existing.data.budget_band || "medium",
        tech_maturity_preference: existing.data.tech_maturity_preference || "mid_stage",
      });
    }
  }, [existing.data?.buyer_id]);

  const mutation = useMutation({
    mutationFn: () => createBuyerProfile({
      org_name: form.org_name.trim(),
      industry: form.industry.trim(),
      technical_areas: form.technical_areas.split(",").map(s => s.trim()).filter(Boolean),
      use_cases: form.use_cases.trim(),
      budget_band: form.budget_band,
      tech_maturity_preference: form.tech_maturity_preference,
    }),
    onSuccess: () => router.push("/marketplace/buyer/recommendations"),
  });

  if (!user) {
    return <div className="card">Please <Link href="/login" className="text-primary underline">sign in</Link>.</div>;
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Buyer profile</h1>
        <p className="text-sm text-muted-foreground">
          Recommendations and inquiry matching read from this profile. Vocabulary
          tip: words like <em>polymer, catalyst, membrane, sensor, battery</em>{" "}
          line up with how patents are tagged — be specific.
        </p>
      </div>

      <div className="card space-y-4">
        <Field label="Organization name">
          <input className="input" value={form.org_name}
            onChange={(e) => setForm({ ...form, org_name: e.target.value })} />
        </Field>
        <Field label="Industry">
          <input className="input"
            placeholder="e.g. Water Treatment, Battery Manufacturing"
            value={form.industry}
            onChange={(e) => setForm({ ...form, industry: e.target.value })} />
        </Field>
        <Field label="Technical areas (comma-separated)">
          <input className="input"
            placeholder="catalyst, membrane, separation"
            value={form.technical_areas}
            onChange={(e) => setForm({ ...form, technical_areas: e.target.value })} />
        </Field>
        <Field label="Use cases (min 100 characters)">
          <textarea className="input min-h-[120px]"
            placeholder="What problem are you solving? Mention specific applications, scale, and constraints."
            value={form.use_cases}
            onChange={(e) => setForm({ ...form, use_cases: e.target.value })} />
          <div className="text-xs text-muted-foreground">
            {form.use_cases.length} characters
            {form.use_cases.length < 100 && " — needs at least 100"}
          </div>
        </Field>
        <div className="grid md:grid-cols-2 gap-3">
          <Field label="Maturity preference">
            <select className="input"
              value={form.tech_maturity_preference}
              onChange={(e) => setForm({ ...form, tech_maturity_preference: e.target.value })}>
              <option value="early_stage">Early stage</option>
              <option value="mid_stage">Mid stage</option>
              <option value="proven">Proven</option>
            </select>
          </Field>
          <Field label="Budget band">
            <select className="input"
              value={form.budget_band}
              onChange={(e) => setForm({ ...form, budget_band: e.target.value })}>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </Field>
        </div>
        <button className="btn-primary w-fit"
          disabled={
            !form.org_name || !form.industry || form.use_cases.length < 100 ||
            !form.technical_areas.trim() || mutation.isPending
          }
          onClick={() => mutation.mutate()}>
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Save className="h-4 w-4 mr-2" />}
          {existing.data ? "Update profile" : "Save and continue to recommendations"}
        </button>
        {mutation.isError && (
          <div className="text-xs text-destructive">
            {(mutation.error as Error)?.message}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-xs text-muted-foreground uppercase tracking-wider">{label}</label>
      {children}
    </div>
  );
}
