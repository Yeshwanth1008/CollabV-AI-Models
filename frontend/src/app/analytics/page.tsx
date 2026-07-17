"use client";

import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getDepartmentReadiness, getHistory, getRetrainStats, runRetrain } from "@/lib/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";

export default function AnalyticsPage() {
  const qc = useQueryClient();
  const readiness = useQuery({ queryKey: ["dept-readiness"], queryFn: getDepartmentReadiness });
  const stats = useQuery({ queryKey: ["retrain-stats"], queryFn: getRetrainStats });
  const history = useQuery({ queryKey: ["history"], queryFn: () => getHistory(20) });

  const retrain = useMutation({
    mutationFn: runRetrain,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["retrain-stats"] });
    },
  });

  const deptData = (readiness.data?.departments ?? []).slice(0, 16).map((d: any) => ({
    dept: d.department.replace("Department of ", "").substring(0, 16),
    score: d.avg_readiness,
  }));

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Analytics</h1>

      <div className="card">
        <h2 className="font-semibold mb-3">Department readiness</h2>
        {readiness.isLoading ? (
          <div className="text-muted-foreground">Loading…</div>
        ) : (
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={deptData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis dataKey="dept" angle={-30} textAnchor="end" interval={0} stroke="#94a3b8" />
              <YAxis stroke="#94a3b8" domain={[0, 100]} />
              <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
              <Bar dataKey="score" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="card">
          <h2 className="font-semibold mb-3">Feedback summary</h2>
          {stats.isLoading ? "…" : (
            <div className="space-y-2 text-sm">
              <div>Total feedback: <span className="font-mono">{stats.data?.analysis.total_feedback}</span></div>
              <div>Accept rate: <span className="font-mono text-success">{((stats.data?.analysis.accept_rate ?? 0) * 100).toFixed(0)}%</span></div>
              <div>Reject rate: <span className="font-mono text-destructive">{((stats.data?.analysis.reject_rate ?? 0) * 100).toFixed(0)}%</span></div>
              <div className="text-xs text-muted-foreground">
                Sufficient for retraining: {stats.data?.analysis.sufficient_data ? "yes" : "no"}
              </div>
              <button
                className="btn-primary text-xs mt-3"
                disabled={!stats.data?.analysis.sufficient_data || retrain.isPending}
                onClick={() => retrain.mutate()}
              >
                {retrain.isPending ? "Retraining…" : "Run retraining"}
              </button>
              {retrain.data && (
                <div className="text-xs mt-2 text-muted-foreground">
                  Improvement: {retrain.data.improvement_score}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="card">
          <h2 className="font-semibold mb-3">Recent matches</h2>
          {history.isLoading ? "…" : (
            <ul className="space-y-2 text-sm max-h-72 overflow-auto">
              {(history.data?.history ?? []).map((h: any) => (
                <li key={h.match_id} className="border-l-2 border-primary pl-3">
                  <div className="font-medium truncate">{h.company_name}</div>
                  <div className="text-xs text-muted-foreground">
                    Top: {h.top_professor} · {h.top_department} · {h.top_score?.toFixed(0)}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
