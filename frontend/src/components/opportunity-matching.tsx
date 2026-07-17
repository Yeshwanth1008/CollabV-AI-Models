"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Send, Sparkles } from "lucide-react";
import {
  createResearchOpportunity, getProfessorResearchOpportunities,
  getOpportunityCandidates, getOpportunityInsights, inviteStudent,
  getResumeDownloadUrl,
  OPPORTUNITY_TYPE_LABELS,
  type ResearchOpportunity, type OpportunityType, type OpportunityCandidate,
} from "@/lib/api";
import { cn, scoreColor } from "@/lib/utils";

const OPPORTUNITY_TYPES: OpportunityType[] = [
  "research_internship", "masters", "phd", "postdoctoral", "research_assistant",
  "thesis_dissertation", "lab_position", "collaborative_project",
  "visiting_researcher", "fellowship", "summer_winter_program", "other",
];

const EMPTY_OPPORTUNITY = {
  title: "", description: "", opportunity_type: "research_internship" as OpportunityType,
  degree_level: "", research_areas: [] as string[], required_skills: [] as string[],
  preferred_skills: [] as string[], required_qualifications: [] as string[],
  preferred_qualifications: [] as string[], min_experience_years: 0, education_requirement: "",
  publications_expected: false, keywords: [] as string[], domain_tags: [] as string[],
  duration: "", stipend_or_funding: "", location: "", is_remote: false, university: "IIT Madras",
};

/** Professor Dashboard: "post a research opportunity" form + list of a
 * professor's own opportunities, each expandable into the AI Matching
 * Engine 8 ranked-candidates panel. Form pattern cloned from
 * InstituteBuyerView's create-form (professor-dashboard/page.tsx). */
export function ResearchOpportunityManager({
  professorId, professorName, department,
}: { professorId: string; professorName: string; department: string }) {
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(EMPTY_OPPORTUNITY);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["professor-opportunities", professorId],
    queryFn: () => getProfessorResearchOpportunities(professorId),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createResearchOpportunity({
        ...form,
        professor_id: professorId, professor_name: professorName, department,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["professor-opportunities", professorId] });
      setForm(EMPTY_OPPORTUNITY);
      setCreating(false);
    },
  });

  const setListField = (key: keyof typeof EMPTY_OPPORTUNITY, value: string) =>
    setForm((f) => ({ ...f, [key]: value.split(",").map((s) => s.trim()).filter(Boolean) }));

  const opportunities = data?.opportunities ?? [];

  return (
    <div className="space-y-4">
      <div className="card space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" /> Research Opportunities
          </h3>
          <button className="btn-primary text-sm" onClick={() => setCreating((c) => !c)}>
            {creating ? "Cancel" : "Post a Research Opportunity"}
          </button>
        </div>

        {creating && (
          <div className="space-y-2 pt-2 border-t border-border">
            <input
              className="input w-full" placeholder="Title (e.g. PhD Position in Robotics)"
              value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            />
            <textarea
              className="input w-full" rows={3} placeholder="Description"
              value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            />
            <div className="grid md:grid-cols-2 gap-2">
              <select
                className="input" value={form.opportunity_type}
                onChange={(e) => setForm((f) => ({ ...f, opportunity_type: e.target.value as OpportunityType }))}
              >
                {OPPORTUNITY_TYPES.map((t) => <option key={t} value={t}>{OPPORTUNITY_TYPE_LABELS[t]}</option>)}
              </select>
              <select
                className="input" value={form.degree_level}
                onChange={(e) => setForm((f) => ({ ...f, degree_level: e.target.value }))}
              >
                <option value="">Degree level</option>
                <option value="undergraduate">Undergraduate</option>
                <option value="masters">Master's</option>
                <option value="phd">PhD</option>
                <option value="postdoc">Postdoc</option>
              </select>
            </div>
            <div className="grid md:grid-cols-2 gap-2">
              <input
                className="input" placeholder="Research areas (comma-separated)"
                value={form.research_areas.join(", ")} onChange={(e) => setListField("research_areas", e.target.value)}
              />
              <input
                className="input" placeholder="Required skills (comma-separated)"
                value={form.required_skills.join(", ")} onChange={(e) => setListField("required_skills", e.target.value)}
              />
              <input
                className="input" placeholder="Preferred skills (comma-separated)"
                value={form.preferred_skills.join(", ")} onChange={(e) => setListField("preferred_skills", e.target.value)}
              />
              <input
                className="input" placeholder="Required qualifications (comma-separated)"
                value={form.required_qualifications.join(", ")} onChange={(e) => setListField("required_qualifications", e.target.value)}
              />
              <input
                className="input" placeholder="Preferred qualifications (comma-separated)"
                value={form.preferred_qualifications.join(", ")} onChange={(e) => setListField("preferred_qualifications", e.target.value)}
              />
              <input
                className="input" placeholder="Keywords (comma-separated)"
                value={form.keywords.join(", ")} onChange={(e) => setListField("keywords", e.target.value)}
              />
              <input
                className="input" placeholder="Duration (e.g. 4-5 years)"
                value={form.duration} onChange={(e) => setForm((f) => ({ ...f, duration: e.target.value }))}
              />
              <input
                className="input" placeholder="Location"
                value={form.location} onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox" checked={form.is_remote}
                onChange={(e) => setForm((f) => ({ ...f, is_remote: e.target.checked }))}
              />
              Remote-friendly
            </label>
            <button
              className="btn-primary text-sm" disabled={!form.title.trim() || !form.description.trim() || createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
              Post Opportunity
            </button>
            {createMutation.isError && <div className="text-xs text-destructive">{(createMutation.error as Error).message}</div>}
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Loading opportunities…</div>
      ) : opportunities.length === 0 ? (
        <div className="card text-sm text-muted-foreground">No research opportunities posted yet.</div>
      ) : (
        <div className="space-y-2">
          {opportunities.map((opp) => (
            <div key={opp.opportunity_id} className="card space-y-2">
              <div
                className="flex items-center justify-between gap-2 cursor-pointer"
                onClick={() => setExpandedId((id) => (id === opp.opportunity_id ? null : opp.opportunity_id))}
              >
                <div>
                  <div className="text-sm font-medium">{opp.title}</div>
                  <div className="text-[11px] text-muted-foreground">
                    {OPPORTUNITY_TYPE_LABELS[opp.opportunity_type]} · {opp.degree_level || "any level"} · {opp.status}
                  </div>
                </div>
                <span className="text-xs text-primary">{expandedId === opp.opportunity_id ? "Hide" : "View Candidates"}</span>
              </div>
              {expandedId === opp.opportunity_id && (
                <MatchingCandidatesPanel
                  opportunityId={opp.opportunity_id} opportunityTitle={opp.title}
                  professorId={professorId} professorName={professorName}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const CONFIDENCE_STYLE: Record<string, string> = {
  high: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  medium: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  low: "bg-muted text-muted-foreground border-transparent",
};

/** Ranked candidate students for one research opportunity - the professor-
 * facing direction of AI Matching Engine 8, structurally cloned from
 * MarketPatentPanel/AudienceCandidateRow (market-patent.tsx). */
export function MatchingCandidatesPanel({
  opportunityId, opportunityTitle, professorId, professorName,
}: { opportunityId: string; opportunityTitle: string; professorId: string; professorName: string }) {
  const [minScore, setMinScore] = useState(0);
  const [degreeLevel, setDegreeLevel] = useState("");
  const [researchArea, setResearchArea] = useState("");
  const [skill, setSkill] = useState("");
  const [university, setUniversity] = useState("");
  const [showInsights, setShowInsights] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["opportunity-candidates", opportunityId, minScore, degreeLevel, researchArea, skill, university],
    queryFn: () =>
      getOpportunityCandidates(professorId, opportunityId, {
        min_match_score: minScore || undefined,
        degree_level: degreeLevel || undefined,
        research_area: researchArea || undefined,
        skill: skill || undefined,
        university: university || undefined,
      }),
  });

  return (
    <div className="border-t border-border pt-3 space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> Research Opportunities — Candidate Students
        </h4>
        <button className="text-xs text-primary hover:underline" onClick={() => setShowInsights((s) => !s)}>
          {showInsights ? "Hide" : "Show"} AI Insights
        </button>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        <select className="input !py-1 text-xs" value={minScore} onChange={(e) => setMinScore(Number(e.target.value))}>
          <option value={0}>Any match score</option>
          <option value={40}>40%+ match</option>
          <option value={60}>60%+ match</option>
          <option value={80}>80%+ match</option>
        </select>
        <select className="input !py-1 text-xs" value={degreeLevel} onChange={(e) => setDegreeLevel(e.target.value)}>
          <option value="">Any degree level</option>
          <option value="b.tech">B.Tech</option>
          <option value="m.tech">M.Tech</option>
          <option value="phd">PhD</option>
        </select>
        <input className="input !py-1 text-xs w-32" placeholder="Research area" value={researchArea} onChange={(e) => setResearchArea(e.target.value)} />
        <input className="input !py-1 text-xs w-28" placeholder="Skill" value={skill} onChange={(e) => setSkill(e.target.value)} />
        <input className="input !py-1 text-xs w-32" placeholder="University/Institute" value={university} onChange={(e) => setUniversity(e.target.value)} />
      </div>

      {showInsights && (
        <OpportunityInsightsPanel professorId={professorId} opportunityId={opportunityId} />
      )}

      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Scoring candidate students…</div>
      ) : error ? (
        <div className="text-xs text-destructive">{(error as Error).message}</div>
      ) : data && data.candidates.length > 0 ? (
        <div className="space-y-2">
          {data.candidates.map((c) => (
            <CandidateRow
              key={c.student_id} candidate={c}
              opportunityId={opportunityId} opportunityTitle={opportunityTitle}
              professorId={professorId} professorName={professorName}
            />
          ))}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground">No candidates match these filters yet.</div>
      )}
    </div>
  );
}

function CandidateRow({
  candidate, opportunityId, opportunityTitle, professorId, professorName,
}: {
  candidate: OpportunityCandidate; opportunityId: string; opportunityTitle: string;
  professorId: string; professorName: string;
}) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [showProfile, setShowProfile] = useState(false);

  const mutation = useMutation({
    mutationFn: () => inviteStudent(professorId, opportunityId, candidate.student_id, message),
    onSuccess: () => setOpen(false),
  });

  return (
    <div className="border border-border rounded-lg p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium">{candidate.student_name || candidate.student_id}</div>
          <div className="text-[11px] text-muted-foreground">{candidate.institute} · {candidate.field_of_study}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className={cn("text-sm font-mono font-bold", scoreColor(candidate.match_score))}>{Math.round(candidate.match_score)}%</div>
          <span className={cn("px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wide", CONFIDENCE_STYLE[candidate.confidence])}>
            {candidate.confidence}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 text-[10px] text-muted-foreground">
        <span className="px-1.5 py-0.5 rounded bg-muted">skills {Math.round(candidate.skills_score)}</span>
        <span className="px-1.5 py-0.5 rounded bg-muted">semantic {Math.round(candidate.semantic_score)}</span>
        <span className="px-1.5 py-0.5 rounded bg-muted">research fit {Math.round(candidate.research_fit_score)}</span>
        <span className="px-1.5 py-0.5 rounded bg-muted">experience {Math.round(candidate.experience_score)}</span>
        <span className="px-1.5 py-0.5 rounded bg-muted">qualifications {Math.round(candidate.qualifications_score)}</span>
        <span className="px-1.5 py-0.5 rounded bg-muted">keywords {Math.round(candidate.keywords_score)}</span>
      </div>

      {candidate.matching_skills.length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {candidate.matching_skills.slice(0, 6).map((s) => (
            <span key={s} className="px-1.5 py-0.5 rounded bg-success/10 text-success">{s}</span>
          ))}
        </div>
      )}
      {candidate.missing_skills.length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {candidate.missing_skills.slice(0, 6).map((s) => (
            <span key={s} className="px-1.5 py-0.5 rounded bg-warning/10 text-warning">{s}</span>
          ))}
        </div>
      )}

      {candidate.reasons.length > 0 && (
        <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
          {candidate.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}

      <div className="flex flex-wrap gap-3 pt-1 text-xs">
        <button className="text-primary hover:underline" onClick={() => setShowProfile((s) => !s)}>
          {showProfile ? "Hide Profile" : "View Profile"}
        </button>
        {candidate.resume_file_path ? (
          <a
            className="text-primary hover:underline" target="_blank" rel="noreferrer"
            href={getResumeDownloadUrl(candidate.student_id)}
          >
            Preview/Download Resume
          </a>
        ) : (
          <span className="text-muted-foreground">No resume on file</span>
        )}
      </div>

      {showProfile && (
        <div className="rounded border border-border bg-muted/30 p-2 text-xs space-y-1">
          {candidate.bio && <p className="text-muted-foreground">{candidate.bio}</p>}
          {candidate.skills.length > 0 && <div>Skills: {candidate.skills.join(", ")}</div>}
          {candidate.education.length > 0 && <div>Education: {candidate.education.join("; ")}</div>}
        </div>
      )}

      {mutation.isSuccess ? (
        <div className="text-xs text-emerald-400">Invitation sent ✓</div>
      ) : open ? (
        <div className="space-y-2">
          <textarea
            className="input w-full text-sm" rows={2}
            placeholder={`Message to ${candidate.student_name || candidate.student_id} about ${opportunityTitle}…`}
            value={message} onChange={(e) => setMessage(e.target.value)}
          />
          <div className="flex gap-2">
            <button className="btn-primary text-xs" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
              {mutation.isPending ? <Loader2 className="h-3 w-3 animate-spin mr-1 inline" /> : <Send className="h-3 w-3 mr-1 inline" />}
              Send Invite
            </button>
            <button className="text-xs text-muted-foreground" onClick={() => setOpen(false)}>Cancel</button>
          </div>
          {mutation.isError && <div className="text-xs text-destructive">{(mutation.error as Error)?.message || "Could not send invite."}</div>}
        </div>
      ) : (
        <button className="btn-primary text-xs" onClick={() => setOpen(true)}>Contact / Invite Student</button>
      )}
    </div>
  );
}

/** Requirement 4: top candidates, near-miss students, strength summaries,
 * suggested keyword updates. */
function OpportunityInsightsPanel({ professorId, opportunityId }: { professorId: string; opportunityId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["opportunity-insights", opportunityId],
    queryFn: () => getOpportunityInsights(professorId, opportunityId),
  });

  if (isLoading) {
    return <div className="flex items-center gap-2 text-muted-foreground text-xs"><Loader2 className="h-3 w-3 animate-spin" /> Generating insights…</div>;
  }
  if (!data) return null;

  return (
    <div className="rounded border border-border bg-muted/30 p-3 text-xs space-y-3">
      <div>
        <div className="font-medium mb-1">Top Candidates</div>
        {data.top_candidates.length === 0 ? (
          <div className="text-muted-foreground">No candidates scored yet.</div>
        ) : (
          <ul className="list-disc list-inside space-y-0.5">
            {data.top_candidates.map((c) => (
              <li key={c.student_id}>{c.student_id} — {Math.round(c.match_score)}% match</li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <div className="font-medium mb-1">Near-Miss Students (need a few more skills)</div>
        {data.near_miss_students.length === 0 ? (
          <div className="text-muted-foreground">None right now.</div>
        ) : (
          <ul className="list-disc list-inside space-y-0.5">
            {data.near_miss_students.map((c) => (
              <li key={c.student_id}>{c.student_id} — {Math.round(c.match_score)}% match, missing {c.missing_skills.slice(0, 3).join(", ") || "a few skills"}</li>
            ))}
          </ul>
        )}
      </div>
      {Object.keys(data.strength_summaries).length > 0 && (
        <div>
          <div className="font-medium mb-1">Strength Summaries</div>
          <div className="space-y-1">
            {Object.entries(data.strength_summaries).map(([studentId, summary]) => (
              <p key={studentId} className="text-muted-foreground">
                <span className="text-foreground">{studentId}:</span> {summary}
              </p>
            ))}
          </div>
        </div>
      )}
      {data.suggested_keyword_updates.length > 0 && (
        <div>
          <div className="font-medium mb-1">Suggested Keyword/Qualification Updates</div>
          <div className="flex flex-wrap gap-1">
            {data.suggested_keyword_updates.map((k) => (
              <span key={k} className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{k}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
