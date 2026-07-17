"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { generateContract, listContractTemplates, parseContract } from "@/lib/api";

export default function ContractsPage() {
  const templates = useQuery({ queryKey: ["templates"], queryFn: listContractTemplates });
  const [type, setType] = useState("joint_research");
  const [companyName, setCompanyName] = useState("");
  const [professorName, setProfessorName] = useState("");
  const [research, setResearch] = useState("");
  const [amount, setAmount] = useState(2500000);
  const [generated, setGenerated] = useState("");

  const [parseText, setParseText] = useState("");
  const [parsed, setParsed] = useState<any>(null);

  const gen = useMutation({
    mutationFn: () =>
      generateContract({
        type, company_name: companyName, professor_name: professorName,
        research_area: research, amount,
      }),
    onSuccess: (data) => setGenerated(data.contract),
  });

  const parse = useMutation({
    mutationFn: () => parseContract(parseText),
    onSuccess: setParsed,
  });

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Contract tools</h1>

      <div className="card space-y-3">
        <h2 className="font-semibold">Generate a template</h2>
        <div className="grid md:grid-cols-2 gap-3">
          <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
            {(templates.data?.templates ?? []).map((t: any) => (
              <option key={t.type} value={t.type}>{t.name}</option>
            ))}
          </select>
          <input className="input" placeholder="Company name" value={companyName} onChange={(e) => setCompanyName(e.target.value)} />
          <input className="input" placeholder="Professor name" value={professorName} onChange={(e) => setProfessorName(e.target.value)} />
          <input className="input" placeholder="Research area" value={research} onChange={(e) => setResearch(e.target.value)} />
          <input className="input" placeholder="Amount (INR)" type="number" value={amount} onChange={(e) => setAmount(parseFloat(e.target.value || "0"))} />
        </div>
        <button className="btn-primary" onClick={() => gen.mutate()} disabled={gen.isPending}>
          {gen.isPending ? "Generating…" : "Generate"}
        </button>
        {generated && (
          <pre className="bg-muted/30 border border-border rounded-md p-4 mt-3 text-xs font-mono whitespace-pre-wrap max-h-96 overflow-auto">
            {generated}
          </pre>
        )}
      </div>

      <div className="card space-y-3">
        <h2 className="font-semibold">Parse an existing MoU</h2>
        <textarea
          className="input min-h-[180px] font-mono text-xs"
          placeholder="Paste contract text…"
          value={parseText} onChange={(e) => setParseText(e.target.value)}
        />
        <button className="btn-primary" onClick={() => parse.mutate()} disabled={!parseText.trim() || parse.isPending}>
          {parse.isPending ? "Parsing…" : "Parse"}
        </button>
        {parsed && (
          <pre className="bg-muted/30 border border-border rounded-md p-4 text-xs font-mono whitespace-pre-wrap max-h-96 overflow-auto">
            {JSON.stringify(parsed, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
