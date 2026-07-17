"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles, Upload } from "lucide-react";
import {
  listEmployeeProfiles, getEmployeeProfile, upsertEmployeeProfile, uploadEmployeeResume,
  getEmployeeRecommendations,
  getEmployeeSkillGapAnalysis, getStartupInsights, inquireAboutListing, addToWishlist,
  logMatchInteraction,
  getEmployeeJobMatches, getEmployeeJobMatchSuggestions, applyToJobAsEmployee, getEmployeeApplications,
  getEmployeeOpportunityMatches, getEmployeeOpportunityMatchSuggestions, getEmployeeOpportunityFitExplanation,
  expressInterestAsEmployee, getEmployeeOpportunityInterests,
  type EmployeeProfile, type EmployeePatentMatch, type EmployeeProfessorMatch,
  type JobMatch, type JobEmploymentType,
  type OpportunityMatch, type OpportunityType, OPPORTUNITY_TYPE_LABELS,
} from "@/lib/api";
import { cn, scoreColor } from "@/lib/utils";

const LIST_FIELDS = [
  ["skills", "Technical Skills"],
  ["interests", "Interests"],
  ["education", "Educational Qualifications"],
  ["projects", "Projects"],
  ["publications", "Publications (optional)"],
  ["certifications", "Certifications"],
  ["internships", "Internships"],
  ["work_experience", "Professional Experience"],
  ["industry_expertise", "Industry Expertise"],
  ["innovation_interests", "Innovation Interests"],
  ["startup_interests", "Business / Startup Interests"],
  ["preferred_domains", "Preferred Technology Domains"],
  ["achievements_soft_skills", "Achievements & Soft Skills"],
] as const;

const EMPTY_PROFILE: EmployeeProfile = {
  user_id: "", name: "", company_name: "", job_title: "", industry: "",
  skills: [], interests: [], bio: "",
  education: [], projects: [], publications: [], certifications: [],
  internships: [], work_experience: [], industry_expertise: [], innovation_interests: [],
  startup_interests: [], career_goals: "", preferred_domains: [], achievements_soft_skills: [],
  resume_filename: "", resume_text: "", resume_file_path: "",
};

const TABS = [
  ["profile", "My Profile"],
  ["professors", "Recommended Professors & Patents"],
  ["job-matches", "Job Opportunities"],
  ["opportunities", "Research Opportunities"],
] as const;
type Tab = (typeof TABS)[number][0];

export default function EmployeeDashboardPage() {
  const [employeeIdInput, setEmployeeIdInput] = useState("");
  const [employeeId, setEmployeeId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("profile");

  const { data: existing } = useQuery({
    queryKey: ["employee-profiles"],
    queryFn: listEmployeeProfiles,
  });

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Employee Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Build your professional profile (optionally from a resume upload) and the AI
          Matching Engine recommends professor-owned patents you can license or buy.
        </p>
      </div>

      <div className="card space-y-2">
        <div className="grid md:grid-cols-[1fr_auto] gap-2">
          <input
            className="input"
            placeholder="Employee ID (e.g. EMP-JOHN, any identifier you choose)"
            value={employeeIdInput}
            onChange={(e) => setEmployeeIdInput(e.target.value)}
          />
          <button
            className="btn-primary"
            disabled={!employeeIdInput.trim()}
            onClick={() => setEmployeeId(employeeIdInput.trim())}
          >
            Enter
          </button>
        </div>
        {existing && existing.count > 0 && (
          <div className="pt-1">
            <div className="text-xs text-muted-foreground mb-1">Or pick an existing employee:</div>
            <div className="flex flex-wrap gap-2">
              {existing.profiles.map((p) => (
                <button
                  key={p.user_id}
                  className="px-3 py-1.5 rounded border border-border text-sm hover:border-primary/60 hover:bg-primary/5 transition"
                  onClick={() => { setEmployeeIdInput(p.user_id); setEmployeeId(p.user_id); }}
                >
                  {p.name || p.user_id}
                  <span className="text-muted-foreground text-xs ml-1">· {p.job_title}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {employeeId && (
        <>
          <div className="flex flex-wrap gap-2 border-b border-border">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={cn(
                  "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition",
                  tab === key ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "profile" && <ProfileTab employeeId={employeeId} />}
          {tab === "professors" && <ProfessorsTab employeeId={employeeId} />}
          {tab === "job-matches" && <JobMatchesTab employeeId={employeeId} />}
          {tab === "opportunities" && <OpportunityMatchesTab employeeId={employeeId} />}
        </>
      )}
    </div>
  );
}

function ProfileTab({ employeeId }: { employeeId: string }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<EmployeeProfile>({ ...EMPTY_PROFILE, user_id: employeeId });
  const [editing, setEditing] = useState(false);
  const [resumeNotice, setResumeNotice] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: profile, isLoading } = useQuery({
    queryKey: ["employee-profile", employeeId],
    queryFn: () => getEmployeeProfile(employeeId),
    retry: false,
  });

  useEffect(() => {
    if (profile) setForm(profile);
    else setForm({ ...EMPTY_PROFILE, user_id: employeeId });
  }, [profile, employeeId]);

  const saveMutation = useMutation({
    mutationFn: () => upsertEmployeeProfile({ ...form, user_id: employeeId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["employee-profile", employeeId] });
      qc.invalidateQueries({ queryKey: ["employee-profiles"] });
      qc.invalidateQueries({ queryKey: ["employee-overview", employeeId] });
      qc.invalidateQueries({ queryKey: ["employee-recommendations", employeeId] });
      qc.invalidateQueries({ queryKey: ["employee-job-matches", employeeId] });
      qc.invalidateQueries({ queryKey: ["employee-opportunity-matches", employeeId] });
      setEditing(false);
    },
  });

  const resumeMutation = useMutation({
    mutationFn: (file: File) => uploadEmployeeResume(file, employeeId),
    onSuccess: (result) => {
      setForm((f) => ({
        ...f,
        skills: Array.from(new Set([...f.skills, ...result.skills])),
        education: result.education.length ? result.education : f.education,
        projects: Array.from(new Set([...f.projects, ...result.projects])),
        publications: Array.from(new Set([...f.publications, ...result.publications])),
        certifications: Array.from(new Set([...f.certifications, ...result.certifications])),
        internships: Array.from(new Set([...f.internships, ...result.internships])),
        work_experience: Array.from(new Set([...f.work_experience, ...result.work_experience])),
        innovation_interests: Array.from(new Set([...f.innovation_interests, ...result.research_interests])),
        career_goals: result.career_goals || f.career_goals,
        preferred_domains: Array.from(new Set([...f.preferred_domains, ...result.preferred_domains])),
        achievements_soft_skills: Array.from(new Set([...f.achievements_soft_skills, ...result.achievements_soft_skills])),
        resume_filename: result.resume_filename,
        resume_text: result.resume_text,
        resume_file_path: result.resume_file_path || f.resume_file_path,
      }));
      setEditing(true);
      setResumeNotice(
        result.extraction_quality === "low_text"
          ? "Couldn't extract readable text from this file - please fill the form manually."
          : "Resume parsed - review the pre-filled fields below and save when ready.",
      );
    },
    onError: (err: any) => setResumeNotice(err?.response?.data?.message || "Resume upload failed."),
  });

  const setListField = (key: keyof EmployeeProfile, value: string) =>
    setForm((f) => ({ ...f, [key]: value.split(",").map((s) => s.trim()).filter(Boolean) }));

  if (isLoading) return <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Loading profile…</div>;

  if (!profile && !editing) {
    return (
      <div className="card space-y-3">
        <div className="text-sm text-muted-foreground">No profile on file for {employeeId} yet.</div>
        <div className="flex gap-2">
          <button className="btn-primary text-sm" onClick={() => setEditing(true)}>Create Profile Manually</button>
          <button
            className="text-sm border border-border rounded px-3 py-1.5 flex items-center gap-1.5 hover:border-primary/60"
            disabled={resumeMutation.isPending}
            onClick={() => fileInputRef.current?.click()}
          >
            {resumeMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            Upload Resume (PDF/DOCX)
          </button>
        </div>
        <input
          ref={fileInputRef} type="file" accept=".pdf,.docx" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) resumeMutation.mutate(f); }}
        />
        {resumeNotice && <div className="text-xs text-muted-foreground">{resumeNotice}</div>}
      </div>
    );
  }

  if (!editing && profile) {
    return (
      <div className="card space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">{profile.name || employeeId}</h2>
          <button className="text-primary text-sm hover:underline" onClick={() => setEditing(true)}>Edit profile</button>
        </div>
        <div className="text-xs text-muted-foreground">{profile.job_title} · {profile.company_name} · {profile.industry}</div>
        {profile.bio && <p className="text-sm text-muted-foreground">{profile.bio}</p>}
        {profile.career_goals && <p className="text-xs italic text-muted-foreground">Career goals: {profile.career_goals}</p>}
        <div className="flex flex-wrap gap-1 text-[11px] pt-1">
          {[...profile.skills, ...profile.preferred_domains].slice(0, 10).map((k) => (
            <span key={k} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{k}</span>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">My Profile</h2>
        <button
          className="text-sm border border-border rounded px-3 py-1.5 flex items-center gap-1.5 hover:border-primary/60"
          disabled={resumeMutation.isPending}
          onClick={() => fileInputRef.current?.click()}
        >
          {resumeMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
          Upload Resume (PDF/DOCX)
        </button>
        <input
          ref={fileInputRef} type="file" accept=".pdf,.docx" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) resumeMutation.mutate(f); }}
        />
      </div>
      {resumeNotice && <div className="text-xs text-primary">{resumeNotice}</div>}

      <div className="grid md:grid-cols-2 gap-2">
        <input className="input" placeholder="Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        <input className="input" placeholder="Company name" value={form.company_name} onChange={(e) => setForm((f) => ({ ...f, company_name: e.target.value }))} />
        <input className="input" placeholder="Job title" value={form.job_title} onChange={(e) => setForm((f) => ({ ...f, job_title: e.target.value }))} />
        <input className="input" placeholder="Industry" value={form.industry} onChange={(e) => setForm((f) => ({ ...f, industry: e.target.value }))} />
      </div>
      <textarea className="input w-full" rows={2} placeholder="Bio" value={form.bio} onChange={(e) => setForm((f) => ({ ...f, bio: e.target.value }))} />
      <textarea className="input w-full" rows={2} placeholder="Career goals" value={form.career_goals} onChange={(e) => setForm((f) => ({ ...f, career_goals: e.target.value }))} />
      <div className="grid md:grid-cols-2 gap-2">
        {LIST_FIELDS.map(([key, label]) => (
          <input
            key={key}
            className="input"
            placeholder={`${label} (comma-separated)`}
            value={(form[key] as string[]).join(", ")}
            onChange={(e) => setListField(key, e.target.value)}
          />
        ))}
      </div>
      <div className="flex gap-2">
        <button className="btn-primary text-sm" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
          {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Save profile
        </button>
        {profile && <button className="text-sm text-muted-foreground hover:text-foreground" onClick={() => setEditing(false)}>Cancel</button>}
      </div>
      {saveMutation.isError && <div className="text-xs text-destructive">{(saveMutation.error as Error)?.message}</div>}
    </div>
  );
}

function PatentCard({ patent, employeeId }: { patent: EmployeePatentMatch; employeeId: string }) {
  const [showSkillGap, setShowSkillGap] = useState(false);
  const [showInsights, setShowInsights] = useState(false);
  const [message, setMessage] = useState("");
  const [showContactBox, setShowContactBox] = useState<"contact" | "buy" | "license" | null>(null);

  useEffect(() => {
    logMatchInteraction({
      source_kind: "employee", source_id: employeeId, target_kind: "patent", target_id: patent.listing_id,
      interaction_type: "view", match_score: patent.match_score,
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [employeeId, patent.listing_id]);

  const skillGapQuery = useQuery({
    queryKey: ["employee-skill-gap", employeeId, patent.listing_id],
    queryFn: () => getEmployeeSkillGapAnalysis(employeeId, patent.listing_id),
    enabled: showSkillGap,
  });
  const insightsQuery = useQuery({
    queryKey: ["startup-insights", patent.listing_id],
    queryFn: () => getStartupInsights(patent.listing_id),
    enabled: showInsights,
  });

  const saveMutation = useMutation({
    mutationFn: () => addToWishlist("employee", employeeId, patent.listing_id),
  });
  const actionMutation = useMutation({
    mutationFn: (type: "inquiry" | "purchase_request" | "licensing_request") =>
      inquireAboutListing(patent.listing_id, {
        buyer_type: "employee", buyer_id: employeeId, message,
        match_score: patent.match_score, inquiry_type: type,
      }),
    onSuccess: () => setShowContactBox(null),
  });

  return (
    <div className="card space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium text-sm leading-snug">{patent.patent_title}</div>
          <div className="text-[11px] text-muted-foreground">{patent.professor_name} · {patent.department}</div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <div className={cn("text-sm font-mono font-bold", scoreColor(patent.match_score))}>{Math.round(patent.match_score)}</div>
          <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] uppercase">{patent.confidence}</span>
        </div>
      </div>
      <div className="flex flex-wrap gap-1 text-[11px]">
        <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{patent.technology_domain}</span>
        <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{patent.status}</span>
      </div>
      <div className="text-xs text-muted-foreground">{patent.commercialization_stage}</div>
      <div className="text-xs">
        {patent.asking_price_inr ? `Price: ₹${patent.asking_price_inr.toLocaleString()}` : "Licensing (no fixed sale price)"}
        {" · "}{patent.licensing_terms?.type || "terms on request"}
      </div>
      {patent.industry_applications.length > 0 && (
        <div className="text-xs text-muted-foreground">Industries: {patent.industry_applications.join(", ")}</div>
      )}
      {patent.reasons.length > 0 && (
        <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
          {patent.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      <div className="text-xs text-muted-foreground italic">Business potential: {patent.business_potential}</div>

      <div className="flex flex-wrap gap-2 pt-1 text-xs">
        <button className="text-primary hover:underline" onClick={() => setShowContactBox("contact")}>Contact Professor</button>
        <button className="text-primary hover:underline" onClick={() => setShowContactBox("buy")}>Buy Patent</button>
        <button className="text-primary hover:underline" onClick={() => setShowContactBox("license")}>Request License</button>
        <button className="text-primary hover:underline" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
          {saveMutation.isSuccess ? "Saved ✓" : "Save for Later"}
        </button>
        <button className="text-primary hover:underline" onClick={() => setShowSkillGap((s) => !s)}>Skill Gap Analysis</button>
        <button className="text-primary hover:underline" onClick={() => setShowInsights((s) => !s)}>Innovation &amp; Business Insights</button>
      </div>

      {showContactBox && (
        <div className="space-y-2 pt-1">
          <textarea
            className="input w-full text-sm" rows={2}
            placeholder={showContactBox === "buy" ? "Message about purchasing…" : showContactBox === "license" ? "Message about licensing…" : "Message to professor…"}
            value={message} onChange={(e) => setMessage(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              className="btn-primary text-xs" disabled={actionMutation.isPending}
              onClick={() => actionMutation.mutate(showContactBox === "buy" ? "purchase_request" : showContactBox === "license" ? "licensing_request" : "inquiry")}
            >
              {actionMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin mr-1 inline" /> : null}Send
            </button>
            <button className="text-xs text-muted-foreground" onClick={() => setShowContactBox(null)}>Cancel</button>
          </div>
        </div>
      )}
      {actionMutation.isSuccess && <div className="text-xs text-emerald-400">Request sent ✓</div>}

      {showSkillGap && (
        <div className="rounded border border-border bg-muted/30 p-2 text-xs space-y-1">
          {skillGapQuery.isLoading ? <div className="flex items-center gap-1 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Analyzing skill gap…</div> : skillGapQuery.data && (
            <>
              <div>Readiness: <span className="font-semibold">{skillGapQuery.data.readiness_score}%</span></div>
              {skillGapQuery.data.missing_skills.length > 0 && <div>Missing skills: {skillGapQuery.data.missing_skills.join(", ")}</div>}
              {skillGapQuery.data.recommended_courses.length > 0 && <div>Courses/training: {skillGapQuery.data.recommended_courses.join(", ")}</div>}
              {skillGapQuery.data.suggested_projects.length > 0 && <div>Suggested projects: {skillGapQuery.data.suggested_projects.join(", ")}</div>}
            </>
          )}
        </div>
      )}
      {showInsights && (
        <div className="rounded border border-border bg-muted/30 p-2 text-xs space-y-1">
          {insightsQuery.isLoading ? <div className="flex items-center gap-1 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Generating business insights…</div> : insightsQuery.data && (
            <>
              <div>Market potential: <span className="font-semibold">{insightsQuery.data.market_potential_score}%</span></div>
              <div>Business model: {insightsQuery.data.business_model}</div>
              {insightsQuery.data.startup_ideas.length > 0 && <div>Ideas: {insightsQuery.data.startup_ideas.slice(0, 2).join(" | ")}</div>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ProfessorsTab({ employeeId }: { employeeId: string }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["employee-recommendations", employeeId],
    queryFn: () => getEmployeeRecommendations(employeeId, { top_k_professors: 8, patents_per_professor: 5 }),
    retry: false,
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2"><Sparkles className="h-4 w-4 text-primary" /> Recommended Professors &amp; Patents</h2>
        <button className="btn-primary text-sm" disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Get AI Recommendations
        </button>
      </div>
      {error ? (
        <div className="card border-destructive/40 text-destructive text-sm">{(error as any)?.response?.data?.message || (error as Error).message}</div>
      ) : isLoading && isFetching ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Matching…</div>
      ) : data ? (
        data.recommended_professors.length === 0 ? (
          <div className="card text-sm text-muted-foreground">No matches found yet - complete your profile for better recommendations.</div>
        ) : (
          <div className="space-y-4">
            {data.recommended_professors.map((p) => <ProfessorCard key={p.professor_id} professor={p} employeeId={employeeId} />)}
          </div>
        )
      ) : (
        <div className="card text-sm text-muted-foreground">Click "Get AI Recommendations" to rank professors (and their patents) against your profile.</div>
      )}
    </div>
  );
}

function ProfessorCard({ professor, employeeId }: { professor: EmployeeProfessorMatch; employeeId: string }) {
  const contactMutation = useMutation({
    mutationFn: () => logMatchInteraction({
      source_kind: "employee", source_id: employeeId, target_kind: "professor", target_id: professor.professor_id,
      interaction_type: "view", match_score: professor.max_score,
    }),
  });

  return (
    <div className="card space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium">{professor.professor_name}</div>
          <div className="text-[11px] text-muted-foreground">{professor.department} · {professor.available_patent_count} patents available</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className={cn("text-sm font-mono font-bold", scoreColor(professor.max_score))}>{Math.round(professor.max_score)}</div>
          <span className="text-[10px] text-muted-foreground">avg {Math.round(professor.average_score)}</span>
        </div>
      </div>
      {professor.research_areas.length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {professor.research_areas.slice(0, 3).map((r) => (
            <span key={r} className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{r}</span>
          ))}
        </div>
      )}
      <div className="flex gap-2 pt-1">
        <a href={`/professors/${professor.professor_id}`} className="text-xs text-primary hover:underline">View Profile</a>
        <button className="text-xs text-primary hover:underline" onClick={() => contactMutation.mutate()}>Contact Professor</button>
      </div>

      {professor.featured_patents.length > 0 && (
        <div className="pt-3 mt-1 border-t border-border space-y-2">
          <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
            Matching Patents ({professor.featured_patents.length} of {professor.available_patent_count} available)
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            {professor.featured_patents.map((p) => (
              <PatentCard key={p.listing_id} patent={p} employeeId={employeeId} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const JOB_SORTS = [
  ["match", "Highest Match"],
  ["newest", "Newest Jobs"],
] as const;

function JobMatchesTab({ employeeId }: { employeeId: string }) {
  const [sort, setSort] = useState<"match" | "newest">("match");
  const [employmentType, setEmploymentType] = useState<JobEmploymentType | null>(null);
  const [remoteOnly, setRemoteOnly] = useState(false);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["employee-job-matches", employeeId, sort, employmentType, remoteOnly],
    queryFn: () =>
      getEmployeeJobMatches(employeeId, {
        sort,
        employment_type: employmentType || undefined,
        is_remote: remoteOnly || undefined,
      }),
    retry: false,
  });

  const { data: applications } = useQuery({
    queryKey: ["employee-applications", employeeId],
    queryFn: () => getEmployeeApplications(employeeId),
  });
  const appliedJobIds = new Set((applications?.applications || []).map((a) => a.job_id));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> Job Opportunities
        </h2>
        <button className="btn-primary text-sm" disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Refresh Matches
        </button>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        {JOB_SORTS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setSort(key)}
            className={cn(
              "px-3 py-1.5 rounded border transition",
              sort === key ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:border-primary/60",
            )}
          >
            {label}
          </button>
        ))}
        <button
          onClick={() => setEmploymentType((t) => (t === "internship" ? null : "internship"))}
          className={cn(
            "px-3 py-1.5 rounded border transition",
            employmentType === "internship" ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:border-primary/60",
          )}
        >
          Internship
        </button>
        <button
          onClick={() => setEmploymentType((t) => (t === "full_time" ? null : "full_time"))}
          className={cn(
            "px-3 py-1.5 rounded border transition",
            employmentType === "full_time" ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:border-primary/60",
          )}
        >
          Full-Time
        </button>
        <button
          onClick={() => setRemoteOnly((r) => !r)}
          className={cn(
            "px-3 py-1.5 rounded border transition",
            remoteOnly ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:border-primary/60",
          )}
        >
          Remote
        </button>
      </div>

      {error ? (
        <div className="card border-destructive/40 text-destructive text-sm">
          {(error as any)?.response?.data?.message || (error as Error).message}
        </div>
      ) : isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Matching your profile against open roles…</div>
      ) : data && data.matches.length > 0 ? (
        <div className="grid md:grid-cols-2 gap-3">
          {data.matches.map((m) => (
            <JobMatchCard key={m.job_id} match={m} employeeId={employeeId} alreadyApplied={appliedJobIds.has(m.job_id)} />
          ))}
        </div>
      ) : (
        <div className="card text-sm text-muted-foreground">
          No job matches yet - complete your profile (or upload a resume) for AI-ranked job recommendations.
        </div>
      )}
    </div>
  );
}

function JobMatchCard({ match, employeeId, alreadyApplied }: { match: JobMatch; employeeId: string; alreadyApplied: boolean }) {
  const qc = useQueryClient();
  const [showSuggestions, setShowSuggestions] = useState(false);

  const suggestionsQuery = useQuery({
    queryKey: ["employee-job-suggestions", employeeId, match.job_id],
    queryFn: () => getEmployeeJobMatchSuggestions(employeeId, match.job_id),
    enabled: showSuggestions,
  });

  const applyMutation = useMutation({
    mutationFn: () => applyToJobAsEmployee(employeeId, match.job_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employee-applications", employeeId] }),
  });
  const applied = alreadyApplied || applyMutation.isSuccess;

  return (
    <div className="card space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium text-sm leading-snug">{match.title}</div>
          <div className="text-[11px] text-muted-foreground">{match.company_name}</div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <div className={cn("text-sm font-mono font-bold", scoreColor(match.match_score))}>{Math.round(match.match_score)}%</div>
          <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] uppercase">{match.confidence}</span>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 text-[11px]">
        <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">
          {match.employment_type === "internship" ? "Internship" : "Full-Time"}
        </span>
        <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
          {match.is_remote ? "Remote" : match.location || "On-site"}
        </span>
      </div>

      {match.matching_skills.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Matching skills</div>
          <div className="flex flex-wrap gap-1 text-[11px]">
            {match.matching_skills.slice(0, 8).map((s) => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-success/10 text-success">{s}</span>
            ))}
          </div>
        </div>
      )}
      {match.missing_skills.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Missing skills</div>
          <div className="flex flex-wrap gap-1 text-[11px]">
            {match.missing_skills.slice(0, 8).map((s) => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-warning/10 text-warning">{s}</span>
            ))}
          </div>
        </div>
      )}

      {match.reasons.length > 0 && (
        <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
          {match.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}

      <div className="flex flex-wrap gap-2 pt-1 text-xs">
        <button
          className="btn-primary text-xs" disabled={applied || applyMutation.isPending}
          onClick={() => applyMutation.mutate()}
        >
          {applyMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin mr-1 inline" /> : null}
          {applied ? "Applied ✓" : "Apply Now"}
        </button>
        <button className="text-primary hover:underline" onClick={() => setShowSuggestions((s) => !s)}>
          AI Suggestions to Improve Match
        </button>
      </div>
      {applyMutation.isError && <div className="text-xs text-destructive">{(applyMutation.error as any)?.response?.data?.message || "Couldn't apply - try again."}</div>}

      {showSuggestions && (
        <div className="rounded border border-border bg-muted/30 p-2 text-xs space-y-1">
          {suggestionsQuery.isLoading ? (
            <div className="flex items-center gap-1 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Generating suggestions…</div>
          ) : suggestionsQuery.data && (
            <>
              {suggestionsQuery.data.skills_to_learn.length > 0 && (
                <div>Skills to learn: {suggestionsQuery.data.skills_to_learn.join(", ")}</div>
              )}
              {suggestionsQuery.data.resume_suggestions.length > 0 && (
                <div>Resume tips: {suggestionsQuery.data.resume_suggestions.join(" | ")}</div>
              )}
              {suggestionsQuery.data.recommended_courses_certs.length > 0 && (
                <div>Courses/certs: {suggestionsQuery.data.recommended_courses_certs.join(", ")}</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

const OPPORTUNITY_TYPES: OpportunityType[] = [
  "research_internship", "masters", "phd", "postdoctoral", "research_assistant",
  "thesis_dissertation", "lab_position", "collaborative_project",
  "visiting_researcher", "fellowship", "summer_winter_program", "other",
];
const DEGREE_LEVELS = ["undergraduate", "masters", "phd", "postdoc"] as const;

function OpportunityMatchesTab({ employeeId }: { employeeId: string }) {
  const [sort, setSort] = useState<"match" | "newest">("match");
  const [opportunityType, setOpportunityType] = useState<OpportunityType | "">("");
  const [degreeLevel, setDegreeLevel] = useState<string>("");

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["employee-opportunity-matches", employeeId, sort, opportunityType, degreeLevel],
    queryFn: () =>
      getEmployeeOpportunityMatches(employeeId, {
        sort,
        opportunity_type: opportunityType || undefined,
        degree_level: degreeLevel || undefined,
      }),
    retry: false,
  });

  const { data: interests } = useQuery({
    queryKey: ["employee-interests", employeeId],
    queryFn: () => getEmployeeOpportunityInterests(employeeId),
  });
  const interestedOpportunityIds = new Set((interests?.interests || []).map((i) => i.opportunity_id));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> Research Opportunities
        </h2>
        <button className="btn-primary text-sm" disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Refresh Matches
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button
          onClick={() => setSort("match")}
          className={cn("px-3 py-1.5 rounded border transition", sort === "match" ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:border-primary/60")}
        >
          Highest Match
        </button>
        <button
          onClick={() => setSort("newest")}
          className={cn("px-3 py-1.5 rounded border transition", sort === "newest" ? "border-primary text-primary bg-primary/5" : "border-border text-muted-foreground hover:border-primary/60")}
        >
          Newest
        </button>
        <select
          className="input !py-1.5 text-xs"
          value={opportunityType}
          onChange={(e) => setOpportunityType(e.target.value as OpportunityType | "")}
        >
          <option value="">All types</option>
          {OPPORTUNITY_TYPES.map((t) => <option key={t} value={t}>{OPPORTUNITY_TYPE_LABELS[t]}</option>)}
        </select>
        <select className="input !py-1.5 text-xs" value={degreeLevel} onChange={(e) => setDegreeLevel(e.target.value)}>
          <option value="">All degree levels</option>
          {DEGREE_LEVELS.map((d) => <option key={d} value={d}>{d[0].toUpperCase() + d.slice(1)}</option>)}
        </select>
      </div>

      {error ? (
        <div className="card border-destructive/40 text-destructive text-sm">
          {(error as any)?.response?.data?.message || (error as Error).message}
        </div>
      ) : isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Matching your profile against research opportunities…</div>
      ) : data && data.matches.length > 0 ? (
        <div className="grid md:grid-cols-2 gap-3">
          {data.matches.map((m) => (
            <OpportunityMatchCard
              key={m.opportunity_id} match={m} employeeId={employeeId}
              alreadyInterested={interestedOpportunityIds.has(m.opportunity_id)}
            />
          ))}
        </div>
      ) : (
        <div className="card text-sm text-muted-foreground">
          No opportunity matches yet - complete your profile (or upload a resume) for AI-ranked research opportunities.
        </div>
      )}
    </div>
  );
}

function OpportunityMatchCard({
  match, employeeId, alreadyInterested,
}: { match: OpportunityMatch; employeeId: string; alreadyInterested: boolean }) {
  const qc = useQueryClient();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showFit, setShowFit] = useState(false);

  const suggestionsQuery = useQuery({
    queryKey: ["employee-opportunity-suggestions", employeeId, match.opportunity_id],
    queryFn: () => getEmployeeOpportunityMatchSuggestions(employeeId, match.opportunity_id),
    enabled: showSuggestions,
  });
  const fitQuery = useQuery({
    queryKey: ["employee-opportunity-fit", employeeId, match.opportunity_id],
    queryFn: () => getEmployeeOpportunityFitExplanation(employeeId, match.opportunity_id),
    enabled: showFit,
  });

  const interestMutation = useMutation({
    mutationFn: () => expressInterestAsEmployee(employeeId, match.opportunity_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["employee-interests", employeeId] }),
  });
  const interested = alreadyInterested || interestMutation.isSuccess;

  return (
    <div className="card space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium text-sm leading-snug">{match.title}</div>
          <div className="text-[11px] text-muted-foreground">{match.professor_name} · {match.department}</div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <div className={cn("text-sm font-mono font-bold", scoreColor(match.match_score))}>{Math.round(match.match_score)}%</div>
          <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] uppercase">{match.confidence}</span>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 text-[11px]">
        <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{OPPORTUNITY_TYPE_LABELS[match.opportunity_type]}</span>
        {match.degree_level && <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{match.degree_level}</span>}
        <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
          {match.is_remote ? "Remote" : match.location || "On-site"}
        </span>
        {match.duration && <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{match.duration}</span>}
      </div>

      {match.matching_skills.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Matching skills</div>
          <div className="flex flex-wrap gap-1 text-[11px]">
            {match.matching_skills.slice(0, 8).map((s) => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-success/10 text-success">{s}</span>
            ))}
          </div>
        </div>
      )}
      {match.missing_skills.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Missing skills</div>
          <div className="flex flex-wrap gap-1 text-[11px]">
            {match.missing_skills.slice(0, 8).map((s) => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-warning/10 text-warning">{s}</span>
            ))}
          </div>
        </div>
      )}

      {match.reasons.length > 0 && (
        <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
          {match.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}

      <div className="flex flex-wrap gap-2 pt-1 text-xs">
        <button
          className="btn-primary text-xs" disabled={interested || interestMutation.isPending}
          onClick={() => interestMutation.mutate()}
        >
          {interestMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin mr-1 inline" /> : null}
          {interested ? "Interested ✓" : "Express Interest"}
        </button>
        <button className="text-primary hover:underline" onClick={() => setShowSuggestions((s) => !s)}>
          AI Suggestions to Improve Match
        </button>
        <button className="text-primary hover:underline" onClick={() => setShowFit((s) => !s)}>
          Why This Is a Good Fit
        </button>
      </div>
      {interestMutation.isError && <div className="text-xs text-destructive">{(interestMutation.error as any)?.response?.data?.message || "Couldn't express interest - try again."}</div>}

      {showSuggestions && (
        <div className="rounded border border-border bg-muted/30 p-2 text-xs space-y-1">
          {suggestionsQuery.isLoading ? (
            <div className="flex items-center gap-1 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Generating suggestions…</div>
          ) : suggestionsQuery.data && (
            <>
              {suggestionsQuery.data.skills_to_learn.length > 0 && (
                <div>Skills to learn: {suggestionsQuery.data.skills_to_learn.join(", ")}</div>
              )}
              {suggestionsQuery.data.resume_suggestions.length > 0 && (
                <div>Resume tips: {suggestionsQuery.data.resume_suggestions.join(" | ")}</div>
              )}
              {suggestionsQuery.data.recommended_courses_certs.length > 0 && (
                <div>Courses/certs: {suggestionsQuery.data.recommended_courses_certs.join(", ")}</div>
              )}
            </>
          )}
        </div>
      )}

      {showFit && (
        <div className="rounded border border-border bg-muted/30 p-2 text-xs space-y-1">
          {fitQuery.isLoading ? (
            <div className="flex items-center gap-1 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Generating explanation…</div>
          ) : fitQuery.data && (
            <>
              <p className="text-muted-foreground">{fitQuery.data.summary}</p>
              {fitQuery.data.key_strengths.length > 0 && (
                <div>Strengths: {fitQuery.data.key_strengths.join(" | ")}</div>
              )}
              {fitQuery.data.potential_gaps.length > 0 && (
                <div>Gaps: {fitQuery.data.potential_gaps.join(" | ")}</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
