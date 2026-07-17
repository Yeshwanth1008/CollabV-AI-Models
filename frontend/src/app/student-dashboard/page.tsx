"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles, Upload } from "lucide-react";
import {
  listStudentProfiles, getStudentProfile, upsertStudentProfile, uploadResume,
  getStudentRecommendations,
  getSkillGapAnalysis, getStartupInsights, inquireAboutListing, addToWishlist,
  logMatchInteraction,
  getJobMatches, getJobMatchSuggestions, applyToJob, getStudentApplications,
  getOpportunityMatches, getOpportunityMatchSuggestions, getOpportunityFitExplanation,
  expressInterest, getStudentInterests,
  type StudentProfile, type StudentPatentMatch, type StudentProfessorMatch,
  type JobMatch, type JobEmploymentType,
  type OpportunityMatch, type OpportunityType, OPPORTUNITY_TYPE_LABELS,
} from "@/lib/api";
import { cn, scoreColor } from "@/lib/utils";

const LIST_FIELDS = [
  ["skills", "Skills"],
  ["interests", "Interests"],
  ["research_areas", "Research Interests"],
  ["education", "Education (one per degree)"],
  ["projects", "Projects"],
  ["publications", "Publications"],
  ["certifications", "Certifications"],
  ["internships", "Internships"],
  ["work_experience", "Work Experience"],
  ["startup_interests", "Startup Interests"],
  ["preferred_domains", "Preferred Technology Domains"],
  ["achievements_soft_skills", "Achievements & Soft Skills"],
] as const;

const EMPTY_PROFILE: StudentProfile = {
  user_id: "", name: "", institute: "", field_of_study: "",
  skills: [], interests: [], research_areas: [], bio: "",
  education: [], projects: [], publications: [], certifications: [],
  internships: [], work_experience: [], startup_interests: [],
  career_goals: "", preferred_domains: [], achievements_soft_skills: [],
  resume_filename: "", resume_text: "", resume_file_path: "",
};

const TABS = [
  ["profile", "My Profile"],
  ["professors", "Recommended Professors & Patents"],
  ["job-matches", "Job Opportunities"],
  ["opportunities", "Research Opportunities"],
] as const;
type Tab = (typeof TABS)[number][0];

export default function StudentDashboardPage() {
  const [studentIdInput, setStudentIdInput] = useState("");
  const [studentId, setStudentId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("profile");

  const { data: existing } = useQuery({
    queryKey: ["student-profiles"],
    queryFn: listStudentProfiles,
  });

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Student Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Build your profile (optionally from a resume upload) and the AI Matching
          Engine recommends professor-owned patents you can license or buy.
        </p>
      </div>

      <div className="card space-y-2">
        <div className="grid md:grid-cols-[1fr_auto] gap-2">
          <input
            className="input"
            placeholder="Student ID (e.g. STU-JANE, any identifier you choose)"
            value={studentIdInput}
            onChange={(e) => setStudentIdInput(e.target.value)}
          />
          <button
            className="btn-primary"
            disabled={!studentIdInput.trim()}
            onClick={() => setStudentId(studentIdInput.trim())}
          >
            Enter
          </button>
        </div>
        {existing && existing.count > 0 && (
          <div className="pt-1">
            <div className="text-xs text-muted-foreground mb-1">Or pick an existing student:</div>
            <div className="flex flex-wrap gap-2">
              {existing.profiles.map((p) => (
                <button
                  key={p.user_id}
                  className="px-3 py-1.5 rounded border border-border text-sm hover:border-primary/60 hover:bg-primary/5 transition"
                  onClick={() => { setStudentIdInput(p.user_id); setStudentId(p.user_id); }}
                >
                  {p.name || p.user_id}
                  <span className="text-muted-foreground text-xs ml-1">· {p.field_of_study}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {studentId && (
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

          {tab === "profile" && <ProfileTab studentId={studentId} />}
          {tab === "professors" && <ProfessorsTab studentId={studentId} />}
          {tab === "job-matches" && <JobMatchesTab studentId={studentId} />}
          {tab === "opportunities" && <OpportunityMatchesTab studentId={studentId} />}
        </>
      )}
    </div>
  );
}

function ProfileTab({ studentId }: { studentId: string }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<StudentProfile>({ ...EMPTY_PROFILE, user_id: studentId });
  const [editing, setEditing] = useState(false);
  const [resumeNotice, setResumeNotice] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: profile, isLoading } = useQuery({
    queryKey: ["student-profile", studentId],
    queryFn: () => getStudentProfile(studentId),
    retry: false,
  });

  useEffect(() => {
    if (profile) setForm(profile);
    else setForm({ ...EMPTY_PROFILE, user_id: studentId });
  }, [profile, studentId]);

  const saveMutation = useMutation({
    mutationFn: () => upsertStudentProfile({ ...form, user_id: studentId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["student-profile", studentId] });
      qc.invalidateQueries({ queryKey: ["student-profiles"] });
      qc.invalidateQueries({ queryKey: ["student-overview", studentId] });
      qc.invalidateQueries({ queryKey: ["student-recommendations", studentId] });
      qc.invalidateQueries({ queryKey: ["job-matches", studentId] });
      qc.invalidateQueries({ queryKey: ["opportunity-matches", studentId] });
      setEditing(false);
    },
  });

  const resumeMutation = useMutation({
    mutationFn: (file: File) => uploadResume(file, studentId),
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
        research_areas: Array.from(new Set([...f.research_areas, ...result.research_interests])),
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

  const setListField = (key: keyof StudentProfile, value: string) =>
    setForm((f) => ({ ...f, [key]: value.split(",").map((s) => s.trim()).filter(Boolean) }));

  if (isLoading) return <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Loading profile…</div>;

  if (!profile && !editing) {
    return (
      <div className="card space-y-3">
        <div className="text-sm text-muted-foreground">No profile on file for {studentId} yet.</div>
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
          <h2 className="text-lg font-semibold">{profile.name || studentId}</h2>
          <button className="text-primary text-sm hover:underline" onClick={() => setEditing(true)}>Edit profile</button>
        </div>
        <div className="text-xs text-muted-foreground">{profile.institute} · {profile.field_of_study}</div>
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
        <input className="input" placeholder="Institute" value={form.institute} onChange={(e) => setForm((f) => ({ ...f, institute: e.target.value }))} />
        <input className="input" placeholder="Field of study" value={form.field_of_study} onChange={(e) => setForm((f) => ({ ...f, field_of_study: e.target.value }))} />
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

function PatentCard({ patent, studentId }: { patent: StudentPatentMatch; studentId: string }) {
  const [showSkillGap, setShowSkillGap] = useState(false);
  const [showInsights, setShowInsights] = useState(false);
  const [message, setMessage] = useState("");
  const [showContactBox, setShowContactBox] = useState<"contact" | "buy" | "license" | null>(null);

  useEffect(() => {
    logMatchInteraction({
      source_kind: "student", source_id: studentId, target_kind: "patent", target_id: patent.listing_id,
      interaction_type: "view", match_score: patent.match_score,
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studentId, patent.listing_id]);

  const skillGapQuery = useQuery({
    queryKey: ["skill-gap", studentId, patent.listing_id],
    queryFn: () => getSkillGapAnalysis(studentId, patent.listing_id),
    enabled: showSkillGap,
  });
  const insightsQuery = useQuery({
    queryKey: ["startup-insights", patent.listing_id],
    queryFn: () => getStartupInsights(patent.listing_id),
    enabled: showInsights,
  });

  const saveMutation = useMutation({
    mutationFn: () => addToWishlist("student", studentId, patent.listing_id),
  });
  const actionMutation = useMutation({
    mutationFn: (type: "inquiry" | "purchase_request" | "licensing_request") =>
      inquireAboutListing(patent.listing_id, {
        buyer_type: "student", buyer_id: studentId, message,
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
        <button className="text-primary hover:underline" onClick={() => setShowInsights((s) => !s)}>Startup Insights</button>
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
              {skillGapQuery.data.recommended_courses.length > 0 && <div>Courses: {skillGapQuery.data.recommended_courses.join(", ")}</div>}
              {skillGapQuery.data.suggested_projects.length > 0 && <div>Suggested projects: {skillGapQuery.data.suggested_projects.join(", ")}</div>}
            </>
          )}
        </div>
      )}
      {showInsights && (
        <div className="rounded border border-border bg-muted/30 p-2 text-xs space-y-1">
          {insightsQuery.isLoading ? <div className="flex items-center gap-1 text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Generating startup insights…</div> : insightsQuery.data && (
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

function ProfessorsTab({ studentId }: { studentId: string }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["student-recommendations", studentId],
    queryFn: () => getStudentRecommendations(studentId, { top_k_professors: 8, patents_per_professor: 5 }),
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
            {data.recommended_professors.map((p) => <ProfessorCard key={p.professor_id} professor={p} studentId={studentId} />)}
          </div>
        )
      ) : (
        <div className="card text-sm text-muted-foreground">Click "Get AI Recommendations" to rank professors (and their patents) against your profile.</div>
      )}
    </div>
  );
}

function ProfessorCard({ professor, studentId }: { professor: StudentProfessorMatch; studentId: string }) {
  const contactMutation = useMutation({
    mutationFn: () => logMatchInteraction({
      source_kind: "student", source_id: studentId, target_kind: "professor", target_id: professor.professor_id,
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
              <PatentCard key={p.listing_id} patent={p} studentId={studentId} />
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

function JobMatchesTab({ studentId }: { studentId: string }) {
  const [sort, setSort] = useState<"match" | "newest">("match");
  const [employmentType, setEmploymentType] = useState<JobEmploymentType | null>(null);
  const [remoteOnly, setRemoteOnly] = useState(false);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["job-matches", studentId, sort, employmentType, remoteOnly],
    queryFn: () =>
      getJobMatches(studentId, {
        sort,
        employment_type: employmentType || undefined,
        is_remote: remoteOnly || undefined,
      }),
    retry: false,
  });

  const { data: applications } = useQuery({
    queryKey: ["student-applications", studentId],
    queryFn: () => getStudentApplications(studentId),
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
            <JobMatchCard key={m.job_id} match={m} studentId={studentId} alreadyApplied={appliedJobIds.has(m.job_id)} />
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

function JobMatchCard({ match, studentId, alreadyApplied }: { match: JobMatch; studentId: string; alreadyApplied: boolean }) {
  const qc = useQueryClient();
  const [showSuggestions, setShowSuggestions] = useState(false);

  const suggestionsQuery = useQuery({
    queryKey: ["job-suggestions", studentId, match.job_id],
    queryFn: () => getJobMatchSuggestions(studentId, match.job_id),
    enabled: showSuggestions,
  });

  const applyMutation = useMutation({
    mutationFn: () => applyToJob(studentId, match.job_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["student-applications", studentId] }),
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

function OpportunityMatchesTab({ studentId }: { studentId: string }) {
  const [sort, setSort] = useState<"match" | "newest">("match");
  const [opportunityType, setOpportunityType] = useState<OpportunityType | "">("");
  const [degreeLevel, setDegreeLevel] = useState<string>("");

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["opportunity-matches", studentId, sort, opportunityType, degreeLevel],
    queryFn: () =>
      getOpportunityMatches(studentId, {
        sort,
        opportunity_type: opportunityType || undefined,
        degree_level: degreeLevel || undefined,
      }),
    retry: false,
  });

  const { data: interests } = useQuery({
    queryKey: ["student-interests", studentId],
    queryFn: () => getStudentInterests(studentId),
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
              key={m.opportunity_id} match={m} studentId={studentId}
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
  match, studentId, alreadyInterested,
}: { match: OpportunityMatch; studentId: string; alreadyInterested: boolean }) {
  const qc = useQueryClient();
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showFit, setShowFit] = useState(false);

  const suggestionsQuery = useQuery({
    queryKey: ["opportunity-suggestions", studentId, match.opportunity_id],
    queryFn: () => getOpportunityMatchSuggestions(studentId, match.opportunity_id),
    enabled: showSuggestions,
  });
  const fitQuery = useQuery({
    queryKey: ["opportunity-fit", studentId, match.opportunity_id],
    queryFn: () => getOpportunityFitExplanation(studentId, match.opportunity_id),
    enabled: showFit,
  });

  const interestMutation = useMutation({
    mutationFn: () => expressInterest(studentId, match.opportunity_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["student-interests", studentId] }),
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
