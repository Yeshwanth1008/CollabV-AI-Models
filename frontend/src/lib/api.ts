import axios from "axios";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ||
  (typeof window !== "undefined" ? "" : "http://localhost:8000");

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
});

// Attach bearer token from auth-store on every request (browser-only).
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.localStorage.getItem("collabv_token");
    if (token) {
      config.headers = config.headers || {};
      (config.headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
    }
  }
  return config;
});

// Normalize API error responses into Error.message so React Query surfaces them.
// Backend shape: {"detail": {"error": "<CODE>", "message": "<text>", ...}}
api.interceptors.response.use(
  (r) => r,
  (err) => {
    const data = err?.response?.data;
    const detail = data?.detail;
    const message =
      (detail && typeof detail === "object" ? detail.message : detail) ||
      data?.message ||
      err?.message;
    const code =
      (detail && typeof detail === "object" ? detail.error : undefined) ||
      data?.error;
    const wrapped = new Error(typeof message === "string" ? message : "Request failed");
    (wrapped as any).code = code;
    (wrapped as any).status = err?.response?.status;
    return Promise.reject(wrapped);
  },
);

// ─── Types ─────────────────────────────────────────────────────────────────

export interface MatchResult {
  professor_name: string;
  professor_id: string;
  department: string;
  score: number;
  tier1_score: number;
  tier2_score: number;
  tier3_score: number;
  patent_score: number;
  readiness_score: number;
  contextual_readiness: number;
  innovation_score?: number;
  kg_domain_score?: number;
  innovation_bridges?: string[];
  skill_score?: number;
  domain_score?: number;
  application_score?: number;
  experience_score?: number;
  collab_readiness_score?: number;
  reasons: string[];
  contact: Record<string, string>;
  deal_assessment?: DealAssessment;
  deal_probability?: number;
  deal_band?: string;
  explanation?: MatchExplanation;
}

export interface DealAssessment {
  professor_id: string;
  professor_name: string;
  success_probability: number;
  success_percent: number;
  confidence_level: string;
  band: string;
  risk_factors: { category: string; description: string; severity: string; mitigation: string }[];
  opportunity_factors: string[];
  recommended_actions: string[];
  estimated_timeline_fit: boolean;
  factor_breakdown: Record<string, number>;
}

export interface MatchExplanation {
  professor_id: string;
  summary: string;
  key_strengths: string[];
  potential_gaps: string[];
  suggested_talking_points: string[];
  confidence: string;
  source: string;
}

export interface MatchRunResponse {
  match_id: string;
  company_id: string;
  company_name: string;
  results: MatchResult[];
  parsed_tags?: Record<string, any>;
}

export interface Professor {
  professor_id: string;
  name: string;
  department: string;
  designation?: string;
  research_areas: string[];
  patent_count?: number;
}

// ─── API calls ────────────────────────────────────────────────────────────

export async function getHealth() {
  return (await api.get("/health")).data;
}

export async function runMatch(payload: {
  raw_text?: string;
  company_id?: string;
  company_name?: string;
  top_k?: number;
  include_deal_score?: boolean;
  include_explanations?: boolean;
  explain_top_k?: number;
}): Promise<MatchRunResponse> {
  return (await api.post("/match/run", payload)).data;
}

export async function getProfessors(department?: string, limit = 50) {
  const params: Record<string, any> = { limit };
  if (department) params.department = department;
  return (await api.get("/professors", { params })).data as {
    count: number;
    professors: Professor[];
  };
}

export async function getProfessor(id: string) {
  return (await api.get(`/professor/${id}`)).data;
}

export async function getProfessorPatents(id: string) {
  return (await api.get(`/professor/${id}/patents`)).data;
}

export interface ProfessorPatentListItem {
  patent_id: string;
  title: string;
  patent_number: string;
  filing_date: string | number;
  status: string;
}

export async function getProfessorPatentsList(id: string) {
  return (await api.get(`/professor/${id}/patents-list`)).data as {
    professor_id: string;
    count: number;
    patents: ProfessorPatentListItem[];
  };
}

export async function getProfessorReadiness(id: string) {
  return (await api.get(`/professor/${id}/readiness`)).data;
}

export async function getDepartmentReadiness() {
  return (await api.get("/readiness/departments")).data;
}

export async function submitFeedback(payload: {
  match_id: string;
  professor_id: string;
  action: string;
  reason?: string;
}) {
  return (await api.post("/feedback/submit", payload)).data;
}

export async function parseContract(text: string) {
  return (await api.post("/contract/parse", { text })).data;
}

export async function generateContract(payload: {
  type: string;
  company_name: string;
  professor_name: string;
  department?: string;
  research_area?: string;
  amount?: number;
  start_date?: string;
  end_date?: string;
  extra?: Record<string, any>;
}) {
  return (await api.post("/contract/generate", payload)).data as { contract: string };
}

export async function listContractTemplates() {
  return (await api.get("/contract/templates")).data;
}

export async function getRetrainStats() {
  return (await api.get("/retrain/stats")).data;
}

export async function runRetrain() {
  return (await api.post("/retrain/run")).data;
}

export async function getHistory(limit = 20) {
  return (await api.get("/history", { params: { limit } })).data;
}

// ─── Auth ─────────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  company_name: string;
  role: string;
  tier: string;
  api_key: string;
  created_at: number;
}

export interface LoginResult {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: AuthUser;
}

export async function login(email: string, password: string): Promise<LoginResult> {
  return (await api.post("/auth/login", { email, password })).data;
}

export async function getMe(): Promise<AuthUser> {
  return (await api.get("/auth/me")).data;
}

// ─── Marketplace: inventor + admin ────────────────────────────────────────

export interface PatentListing {
  listing_id: string;
  professor_id: string;
  title: string;
  patent_number?: string | null;
  indian_patent_number?: string | null;
  abstract?: string | null;
  abstract_status?: string | null;
  claims_text?: string | null;
  inventor_names?: string[];
  granted_date?: string | null;
  licensing_terms?: Record<string, any>;
  asking_price_inr?: number | null;
  domain_tags?: string[];
  industry_tags?: string[];
  status: string;
  created_at?: number;
  updated_at?: number;
  activated_at?: number | null;
  approved_at?: number | null;
  approved_by_user_id?: string | null;
  // Enriched server-side so the UI can suppress activate on stub owners:
  owner_profile_type?: "faculty" | "patent_stub" | string;
  owner_name?: string;
  department?: string;
}

export type ClaimState = "none" | "pending" | "approved" | "rejected";

export interface ClaimRow {
  claim_id: string;
  user_id: string;
  requested_professor_id: string;
  status: ClaimState;
  note?: string | null;
  reviewer_user_id?: string | null;
  review_note?: string | null;
  created_at: number;
  reviewed_at?: number | null;
  // Server-enriched on admin queue:
  requester_email?: string;
  requester_name?: string;
  requested_professor_name?: string;
  requested_professor_dept?: string;
  requested_profile_type?: string;
}

export interface MyListingsResult {
  linked_professor_id: string | null;
  owner_profile_type?: string;
  listings: PatentListing[];
  count?: number;
  note?: string;
  // New: surfaces whether the inventor is linked, pending review, etc.
  claim_state?: ClaimState;
  claim?: ClaimRow | null;
}

export async function getMyListings(): Promise<MyListingsResult> {
  return (await api.get("/marketplace/inventor/listings")).data;
}

export async function getListing(listingId: string): Promise<PatentListing> {
  return (await api.get(`/marketplace/listings/${listingId}`)).data;
}

export interface PatentListingPatch {
  title?: string;
  abstract?: string;
  claims_text?: string;
  inventor_names?: string[];
  licensing_terms?: Record<string, any>;
  asking_price_inr?: number | null;
  domain_tags?: string[];
  industry_tags?: string[];
}

export async function patchListing(listingId: string, patch: PatentListingPatch) {
  return (await api.patch(`/marketplace/listings/${listingId}`, patch)).data;
}

// NOTE: transitions go through the single server-side chokepoint
// (POST .../transition). State-machine gating + the patent_stub admin gate
// are enforced on the server; the UI is purely a thin wrapper.
export async function transitionListing(
  listingId: string,
  target_status: "draft" | "pending_approval" | "active" | "paused" | "withdrawn" | "sold",
  reason?: string,
) {
  return (
    await api.post(`/marketplace/listings/${listingId}/transition`, {
      target_status,
      reason,
    })
  ).data;
}

// Submit a claim request for a faculty professor profile. Returns a PENDING
// claim — the link is only set after an admin approves via
// /marketplace/admin/claim-requests/{id}/review. Idempotent: re-submitting
// the same (user, professor) returns the existing pending/approved row.
export async function claimProfessor(professor_id: string): Promise<{
  claim_id: string; status: ClaimState; requested_professor_id: string;
}> {
  return (await api.post("/marketplace/inventor/claim", { professor_id })).data;
}

export async function getAdminClaimRequests(): Promise<{
  claims: ClaimRow[]; count: number;
}> {
  return (await api.get("/marketplace/admin/claim-requests")).data;
}

export async function reviewClaimRequest(
  claim_id: string, approve: boolean, review_note?: string,
) {
  return (
    await api.post(`/marketplace/admin/claim-requests/${claim_id}/review`,
      { approve, review_note })
  ).data as { claim_id: string; status: ClaimState; linked_professor_id: string | null };
}

export async function getAdminPendingListings(): Promise<{
  listings: PatentListing[];
  count: number;
}> {
  return (await api.get("/marketplace/admin/pending-listings")).data;
}

// ─── Marketplace: public browse + inquiry ─────────────────────────────────

export interface BrowseListingsResult {
  count: number;
  limit: number;
  offset: number;
  has_more: boolean;
  listings: PatentListing[];
}

export async function browseListings(params: {
  domain?: string;
  industry?: string;
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<BrowseListingsResult> {
  return (await api.get("/marketplace/listings", { params })).data;
}

export async function createInquiry(listingId: string, message: string) {
  return (
    await api.post(`/marketplace/listings/${listingId}/inquiry`, { message })
  ).data as { inquiry_id: string; status: string };
}

// ─── Marketplace: Mode B (recommendations) + inbox ───────────────────────

export interface CandidatePatentDTO {
  listing_id: string;
  title: string;
  professor_id: string;
  professor_name?: string;
  department?: string;
  status: string;
  score: number;
  retrieval_score: number;
  domain_overlap_score: number;
  recency_score: number;
  industry_match_score: number;
  licensing_terms?: Record<string, any>;
  asking_price_inr?: number | null;
  reasons?: string[];
}

export interface RecommendationsResult {
  query_hash?: string;
  mode: string;
  subject_id: string;
  total_candidates_considered?: number;
  total_filtered?: number;
  cold_start?: boolean;
  candidates: CandidatePatentDTO[];
  notes?: string[];
  // engine_unavailable means dense retrieval is dark — surface it differently
  // from "no_active_patents" which is the empty-marketplace case.
  status: "ok" | "no_active_patents" | "engine_unavailable";
  message?: string;
  operator_hint?: string;
  engine_status?: any;
}

export async function recommendPatentsForMe(opts: {
  top_k?: number;
} = {}): Promise<RecommendationsResult> {
  return (await api.post("/marketplace/buyer/recommendations", {
    top_k: opts.top_k ?? 20,
    include_explanations: false,
  })).data;
}

export type InquiryStatus = "new" | "acknowledged" | "accepted" | "declined";

export interface InquiryRow {
  inquiry_id: string;
  listing_id: string;
  buyer_id?: string | null;
  user_id: string;
  message?: string | null;
  status: InquiryStatus;
  match_score_at_inquiry?: number | null;
  created_at: number;
  responded_at?: number | null;
  // Enriched server-side:
  listing_title?: string;
  requester_email?: string;
}

export interface InboxResult {
  sent: InquiryRow[];
  received: InquiryRow[];
  is_inventor: boolean;
  counts: { sent: number; received: number };
}

export async function getInbox(): Promise<InboxResult> {
  return (await api.get("/marketplace/inbox")).data;
}

export async function respondToInquiry(
  inquiry_id: string,
  status: "acknowledged" | "accepted" | "declined",
) {
  return (await api.post(`/marketplace/inquiries/${inquiry_id}/respond`,
                         { status })).data as {
    inquiry_id: string; status: InquiryStatus; responded_at: number | null;
  };
}

export async function createBuyerProfile(payload: {
  org_name: string;
  org_type?: string;
  industry: string;
  industries_of_interest?: string[];
  technical_areas: string[];
  use_cases: string;
  tech_maturity_preference?: string;
  budget_band?: string;
  geographic_scope?: string[];
}) {
  return (await api.post("/marketplace/buyers", payload)).data as {
    buyer_id: string; created: boolean;
  };
}

export async function getMyBuyerProfile() {
  return (await api.get("/marketplace/buyers/me")).data;
}

// ─── Matching Engine 2: Professor → Company ───────────────────────────────

export interface CompanyMatchScoreBreakdown {
  research_domain: number;    // /30
  technical_skills: number;   // /25
  ai_methods: number;         // /20
  publications: number;       // /15
  industry_domain: number;    // /10
}

export interface CompanyMatchResult {
  rank: number;
  company_name: string;
  project_title: string;
  industry_domain: string;
  sector: string;
  location: string;
  budget: string;
  timeline: string;
  collaboration_type: string;
  score: number;
  match_level: "Excellent" | "Strong" | "Moderate" | "Weak" | "Poor";
  recommendation: "Highly Recommended" | "Recommended" | "Consider" | "Not Recommended";
  confidence_score: number;
  score_breakdown: CompanyMatchScoreBreakdown;
  matching_research_areas: string[];
  matching_skills: string[];
  matching_technologies: string[];
  matching_ai_techniques: string[];
  matching_keywords: string[];
  missing_skills: string[];
  reasons: string[];
  professor_contribution: string;
  student_roles: string;
  collaboration_potential: string;
}

export interface ProfessorMatchSummary {
  total: number;
  avg_score: number;
  highest_score: number;
  lowest_score: number;
  distribution: {
    excellent: number;
    strong: number;
    moderate: number;
    weak: number;
    poor: number;
  };
  recommended_count: number;
  consider_count: number;
  not_recommended_count: number;
}

export interface ProfessorMatchResponse {
  match_id: string;
  professor_id: string;
  professor_name: string;
  department: string;
  designation: string;
  top_domains: string[];
  results: CompanyMatchResult[];
  summary: ProfessorMatchSummary;
}

export async function runProfessorMatch(
  professor_id: string,
  top_k?: number,
): Promise<ProfessorMatchResponse> {
  return (
    await api.post("/professor-match/run", { professor_id, top_k })
  ).data;
}

export async function listCompanyProjects(): Promise<{
  count: number;
  companies: { company_name: string; industry_domain: string; sector: string; technical_area: string; location: string }[];
}> {
  return (await api.get("/professor-match/companies")).data;
}

// ─── Matching Engine 3 (Patent → Problem Statement) &
//     Matching Engine 4 (Problem Statement → Patent) ─────────────────────
// Powers the "Patent Smart Matches" section on the Professor Dashboard
// (Engine 3) and the Company Dashboard (Engine 4).

export interface ProblemStatement {
  id: string;
  sector: string;
  title: string;
  description: string;
  problem_statement: string;
  expected_outcomes: string[];
}

export interface SmartMatch {
  match_id: string;
  direction: "patent_to_problem" | "problem_to_patent";
  patent_id: string;
  patent_number: string;
  patent_title: string;
  professor_id: string;
  professor_name: string;
  department: string;
  problem_statement_id: string;
  match_score: number;
  score_breakdown: { matching_keywords?: string[]; matching_domains?: string[] };
  reasons: string[];
  dashboard_visibility: boolean;
  model_version: string;
  created_at: number;
  // Enriched only on the professor-dashboard read path:
  problem_title?: string;
  problem_sector?: string;
}

export async function listProblemStatements(): Promise<{
  count: number;
  problem_statements: ProblemStatement[];
}> {
  return (await api.get("/problem-statements")).data;
}

export async function getProblemStatement(id: string): Promise<ProblemStatement> {
  return (await api.get(`/problem-statements/${id}`)).data;
}

export async function runMatchingEngine4(problem_statement_id: string, top_k = 10) {
  return (await api.post("/matching-engine-4/run", { problem_statement_id, top_k }))
    .data as { problem_statement_id: string; count: number; matches: SmartMatch[] };
}

export async function getEngine4Matches(problem_statement_id: string) {
  return (await api.get(`/matching-engine-4/problem/${problem_statement_id}/matches`))
    .data as { problem_statement_id: string; count: number; matches: SmartMatch[] };
}

// ─── Patent Marketplace: Matching Engine 5 (Patent → Audience) + Offers ──
// Powers the Professor Dashboard's "sell this patent" flow: rank a patent
// against companies/students/employees/professors/institutes, then send a
// direct offer to a chosen candidate.

export type AudienceTargetType = "company" | "student" | "employee" | "professor" | "institute";

export interface StudentProfile {
  user_id: string;
  name: string;
  institute: string;
  field_of_study: string;
  skills: string[];
  interests: string[];
  research_areas: string[];
  bio: string;
  education: string[];
  projects: string[];
  publications: string[];
  certifications: string[];
  internships: string[];
  work_experience: string[];
  startup_interests: string[];
  career_goals: string;
  preferred_domains: string[];
  achievements_soft_skills: string[];
  resume_filename: string;
  resume_text: string;
  resume_file_path: string;
}

export interface EmployeeProfile {
  user_id: string;
  name: string;
  company_name: string;
  job_title: string;
  industry: string;
  skills: string[];
  interests: string[];
  bio: string;
  education: string[];
  projects: string[];
  publications: string[];
  certifications: string[];
  internships: string[];
  work_experience: string[];
  industry_expertise: string[];
  innovation_interests: string[];
  startup_interests: string[];
  career_goals: string;
  preferred_domains: string[];
  achievements_soft_skills: string[];
  resume_filename: string;
  resume_text: string;
  resume_file_path: string;
}

export interface InstituteProfile {
  user_id: string;
  institute_name: string;
  focus_areas: string[];
  departments: string[];
  collaboration_types: string[];
  bio: string;
}

// ─── Unified Matching Engine (merged former Engines 3, 5, 6) ──────────────
// One result shape, one endpoint (POST /match), dispatched by
// source_kind/target_kind. Direction-specific fields (e.g. asking_price_inr,
// commercialization_score) are populated only when relevant to that
// direction; otherwise left at their empty/default value.

export interface MatchResult {
  target_kind: string; // "company"|"professor"|"student"|"employee"|"institute"|"listing"|"patent"
  target_id: string;
  target_name: string;
  tag: string;
  score: number;
  semantic_score: number;
  keyword_score: number;
  confidence: "high" | "medium" | "low";
  reasons: string[];
  matching_domains: string[];
  matching_keywords: string[];
  // patent -> audience direction
  next_action: string;
  shared_expertise: string[];
  collaboration_opportunity: string;
  // -> listing direction
  professor_id: string;
  professor_name: string;
  department: string;
  status: string;
  asking_price_inr: number | null;
  licensing_terms: Record<string, any>;
  domain_tags: string[];
  industry_tags: string[];
  // buyer -> raw patent pool direction
  technology_domain: string;
  commercialization_score: number;
  patent_readiness: string;
  suggested_action: string;
  collaboration_mode: string;
  institute: string; // the owning professor's affiliated institute
}

export type MatchTargetKind =
  | "audience" | "company" | "professor" | "student" | "employee" | "institute"
  | "listing" | "patent_pool";

export interface MatchCategoriesResult {
  source_kind: string;
  source_id: string;
  target_kind: "audience";
  embeddings_ready: boolean;
  categories: Record<AudienceTargetType, { label: string; count: number; matches: MatchResult[] }>;
}

export interface MatchListResult {
  source_kind: string;
  source_id: string;
  target_kind: string;
  count: number;
  matches: MatchResult[];
  total_active_listings?: number;
}

export async function runUnifiedMatch(payload: {
  source_kind: string; source_id: string; target_kind: MatchTargetKind;
  top_k?: number; domain?: string; industry?: string; max_price?: number;
}): Promise<MatchListResult> {
  return (await api.post("/match", payload)).data as MatchListResult;
}

/** Categorized AI recommendations across all 5 audience types in one call
 * (Best Matching Companies/Employees/Students/Professors/Institutes). */
export async function getPatentAudienceCategories(patentId: string, top_k = 5) {
  return (
    await api.post("/match", { source_kind: "patent", source_id: patentId, target_kind: "audience", top_k })
  ).data as MatchCategoriesResult;
}

export type MatchInteractionType =
  | "view" | "save" | "bookmark" | "offer_sent" | "offer_accepted" | "offer_declined"
  | "inquiry_sent" | "collaboration_started" | "licensing_request" | "purchase_request"
  | "collaboration_proposal" | "technology_transfer_request";

export async function logMatchInteraction(payload: {
  source_kind: string; source_id: string; target_kind: string; target_id: string;
  interaction_type: MatchInteractionType; match_score?: number;
}) {
  return (await api.post("/match/interactions", payload)).data as { logged: boolean };
}

export interface PatentOffer {
  offer_id: string;
  patent_id: string;
  patent_number: string;
  patent_title: string;
  professor_id: string;
  professor_name: string;
  target_type: AudienceTargetType;
  target_id: string;
  target_name: string;
  match_score: number | null;
  score_breakdown: Record<string, any>;
  reasons: string[];
  message: string;
  status: "sent" | "viewed" | "accepted" | "declined";
  created_at: number;
  responded_at: number | null;
}

export async function upsertStudentProfile(payload: Omit<StudentProfile, never>) {
  return (await api.post("/marketplace/profiles/student", payload)).data as { user_id: string; saved: boolean };
}

export async function getStudentProfile(userId: string) {
  return (await api.get(`/marketplace/profiles/student/${userId}`)).data as StudentProfile;
}

export async function listStudentProfiles() {
  return (await api.get("/marketplace/profiles/students")).data as { count: number; profiles: StudentProfile[] };
}

export async function upsertEmployeeProfile(payload: EmployeeProfile) {
  return (await api.post("/marketplace/profiles/employee", payload)).data as { user_id: string; saved: boolean };
}

export async function getEmployeeProfile(userId: string) {
  return (await api.get(`/marketplace/profiles/employee/${userId}`)).data as EmployeeProfile;
}

export async function listEmployeeProfiles() {
  return (await api.get("/marketplace/profiles/employees")).data as { count: number; profiles: EmployeeProfile[] };
}

export async function upsertInstituteProfile(payload: InstituteProfile) {
  return (await api.post("/marketplace/profiles/institute", payload)).data as { user_id: string; saved: boolean };
}

export async function browseAudience(targetType: AudienceTargetType) {
  return (await api.get(`/marketplace/audience/${targetType}`)).data as {
    target_type: string; count: number; candidates: any[];
  };
}

export async function sendPatentOffer(
  patentId: string,
  payload: {
    professor_id: string;
    target_type: AudienceTargetType;
    target_id: string;
    target_name?: string;
    message?: string;
    match_score?: number;
    score_breakdown?: Record<string, any>;
    reasons?: string[];
  },
) {
  return (
    await api.post(`/marketplace/patents/${encodeURIComponent(patentId)}/offers`, payload)
  ).data as { offer_id: string; status: string };
}

export async function getOffersSent(professorId: string) {
  return (await api.get("/marketplace/patents/offers/sent", { params: { professor_id: professorId } }))
    .data as { professor_id: string; count: number; offers: PatentOffer[] };
}

export async function getOffersReceived(targetType: AudienceTargetType, targetId: string) {
  return (
    await api.get("/marketplace/patents/offers/received", {
      params: { target_type: targetType, target_id: targetId },
    })
  ).data as { target_type: string; target_id: string; count: number; offers: PatentOffer[] };
}

export async function respondToOffer(offerId: string, status: "accepted" | "declined" | "viewed") {
  return (await api.post(`/marketplace/patents/offers/${offerId}/respond`, { status })).data as PatentOffer;
}

// ─── Matching Engine 6 (Audience → Patent Listings) + Buyer Dashboard ────
// Powers the "Discover Patents" buyer-side dashboard for all 5 audience
// types: AI-ranked active patent_listings, purchase/licensing inquiries,
// and wishlist.

export interface ListingInquiry {
  inquiry_id: string;
  listing_id: string;
  listing_title: string;
  professor_id: string;
  professor_name: string;
  buyer_type: AudienceTargetType;
  buyer_id: string;
  buyer_name: string;
  message: string;
  match_score: number | null;
  status: "sent" | "viewed" | "negotiating" | "accepted" | "declined";
  created_at: number;
  responded_at: number | null;
}

/** AI-ranked active patent listings for one buyer of any audience type. */
export async function getDiscoverRecommendations(
  buyerType: AudienceTargetType,
  payload: { buyer_id: string; top_k?: number; domain?: string; industry?: string; max_price?: number },
) {
  return runUnifiedMatch({
    source_kind: buyerType, source_id: payload.buyer_id, target_kind: "listing",
    top_k: payload.top_k, domain: payload.domain, industry: payload.industry, max_price: payload.max_price,
  });
}

export async function inquireAboutListing(
  listingId: string,
  payload: {
    buyer_type: AudienceTargetType; buyer_id: string; buyer_name?: string;
    message?: string; match_score?: number;
    inquiry_type?: "inquiry" | "purchase_request" | "licensing_request";
  },
) {
  return (await api.post(`/marketplace/listings/${listingId}/inquire`, payload))
    .data as { inquiry_id: string; status: string };
}

export async function getListingInquiriesSent(buyerType: AudienceTargetType, buyerId: string) {
  return (
    await api.get("/marketplace/listings/inquiries/sent", {
      params: { buyer_type: buyerType, buyer_id: buyerId },
    })
  ).data as { buyer_type: string; buyer_id: string; count: number; inquiries: ListingInquiry[] };
}

export async function getListingInquiriesReceived(professorId: string) {
  return (
    await api.get("/marketplace/listings/inquiries/received", { params: { professor_id: professorId } })
  ).data as { professor_id: string; count: number; inquiries: ListingInquiry[] };
}

export async function respondListingInquiry(
  inquiryId: string,
  status: "negotiating" | "accepted" | "declined" | "viewed",
) {
  return (await api.post(`/marketplace/listings/inquiries/${inquiryId}/respond`, { status }))
    .data as ListingInquiry;
}

export async function addToWishlist(buyerType: AudienceTargetType, buyerId: string, listingId: string) {
  return (
    await api.post("/marketplace/wishlist", { buyer_type: buyerType, buyer_id: buyerId, listing_id: listingId })
  ).data as { saved: boolean };
}

export async function removeFromWishlist(buyerType: AudienceTargetType, buyerId: string, listingId: string) {
  return (
    await api.post("/marketplace/wishlist/remove", { buyer_type: buyerType, buyer_id: buyerId, listing_id: listingId })
  ).data as { removed: boolean };
}

export async function getWishlist(buyerType: AudienceTargetType, buyerId: string) {
  return (
    await api.get("/marketplace/wishlist", { params: { buyer_type: buyerType, buyer_id: buyerId } })
  ).data as { buyer_type: string; buyer_id: string; count: number; listings: PatentListing[] };
}

// ─── Technology Transfer hub: negotiation threads, requests, history,
//     analytics, notifications ────────────────────────────────────────────

export type ThreadType = "offer" | "inquiry";
export type RoleType = AudienceTargetType;

export interface NegotiationMessage {
  message_id: string;
  thread_type: ThreadType;
  thread_id: string;
  sender_role: string;
  sender_id: string;
  sender_name: string;
  body: string;
  counter_price: number | null;
  counter_terms: Record<string, any> | null;
  created_at: number;
}

export interface TechnologyRequest {
  request_id: string;
  requester_type: RoleType;
  requester_id: string;
  requester_name: string;
  title: string;
  description: string;
  keywords: string[];
  status: "open" | "fulfilled" | "closed";
  created_at: number;
}

export interface TechTransferHistoryItem {
  kind: "patent_offer" | "listing_inquiry";
  direction: "sent" | "received";
  status: string;
  created_at: number;
  [key: string]: any;
}

export interface TechTransferNotification {
  type: "listing_inquiry" | "patent_offer" | "new_listing_match";
  message: string;
  created_at: number | null;
}

export async function sendNegotiationMessage(
  threadType: ThreadType,
  threadId: string,
  payload: {
    sender_role: string; sender_id: string; sender_name?: string; body: string;
    counter_price?: number; counter_terms?: Record<string, any>;
  },
) {
  return (
    await api.post(`/technology-transfer/threads/${threadType}/${threadId}/messages`, payload)
  ).data as { message_id: string };
}

export async function getNegotiationMessages(threadType: ThreadType, threadId: string) {
  return (
    await api.get(`/technology-transfer/threads/${threadType}/${threadId}/messages`)
  ).data as { thread_type: string; thread_id: string; count: number; messages: NegotiationMessage[] };
}

export async function createTechRequest(payload: {
  requester_type: RoleType; requester_id: string; requester_name?: string;
  title: string; description?: string; keywords?: string[];
}) {
  return (await api.post("/technology-transfer/requests", payload)).data as { request_id: string; status: string };
}

export async function browseTechRequests(status: string = "open") {
  return (await api.get("/technology-transfer/requests", { params: { status } }))
    .data as { count: number; requests: TechnologyRequest[] };
}

export async function getTechRequest(requestId: string) {
  return (await api.get(`/technology-transfer/requests/${requestId}`)).data as TechnologyRequest;
}

export async function matchTechRequest(requestId: string, top_k = 10) {
  return (
    await api.get(`/technology-transfer/requests/${requestId}/matches`, { params: { top_k } })
  ).data as { request_id: string; count: number; matches: MatchResult[] };
}

export async function closeTechRequest(requestId: string, status: "fulfilled" | "closed") {
  return (await api.post(`/technology-transfer/requests/${requestId}/close`, { status })).data as TechnologyRequest;
}

export async function getTechTransferHistory(roleType: RoleType, roleId: string) {
  return (
    await api.get("/technology-transfer/history", { params: { role_type: roleType, role_id: roleId } })
  ).data as { role_type: string; role_id: string; count: number; history: TechTransferHistoryItem[] };
}

export async function getTechTransferAnalytics(roleType: RoleType, roleId: string) {
  return (
    await api.get("/technology-transfer/analytics", { params: { role_type: roleType, role_id: roleId } })
  ).data as {
    role_type: string; role_id: string; listings_for_sale: number;
    offers_sent: number; offers_received: number; inquiries_sent: number; inquiries_received: number;
    accepted_count: number; declined_count: number; pending_count: number;
  };
}

export async function getTechTransferNotifications(roleType: RoleType, roleId: string) {
  return (
    await api.get("/technology-transfer/notifications", { params: { role_type: roleType, role_id: roleId } })
  ).data as { role_type: string; role_id: string; count: number; notifications: TechTransferNotification[] };
}

// ─── Company Dashboard (enhanced Engine 4): Company Profile + combined ────
// Professor/Patent recommendations ──────────────────────────────────────

export interface CompanyProfile {
  company_id: string;
  company_name: string;
  description: string;
  industry: string;
  business_domain: string;
  products_services: string[];
  technologies_used: string[];
  tech_stack: string[];
  research_interests: string[];
  business_objectives: string;
  focus_areas: string[];
  keywords: string[];
  market_segment: string;
  innovation_challenges: string;
  strategic_goals: string;
  existing_projects: string[];
  preferred_collaboration_areas: string[];
  company_size: string;
  category: string;
}

export async function upsertCompanyProfile(payload: CompanyProfile) {
  return (await api.post("/marketplace/company-profile", payload)).data as {
    company_id: string; saved: boolean;
  };
}

export async function getCompanyProfile(companyId: string) {
  return (await api.get(`/marketplace/company-profile/${companyId}`)).data as CompanyProfile;
}

export async function listCompanyProfiles() {
  return (await api.get("/marketplace/company-profiles")).data as {
    count: number; profiles: CompanyProfile[];
  };
}

export interface MatchingPatent {
  patent_id: string;
  patent_title: string;
  patent_number: string;
  status: string;
  score: number;
  matching_keywords: string[];
  matching_domains: string[];
  reasons: string[];
}

export interface RecommendedProfessor {
  professor_id: string;
  professor_name: string;
  institution: string;
  department: string;
  research_areas: string[];
  expertise: string[];
  skills: string[];
  publications: string[];
  score: number;
  confidence: "high" | "medium" | "low";
  reasons: string[];
  suggested_collaboration_type: string;
  matching_patents: MatchingPatent[];
}

export interface CompanyRecommendationsResult {
  company_id: string;
  problem_statement_id: string | null;
  used_profile: boolean;
  used_problem_statement: boolean;
  recommended_professors: RecommendedProfessor[];
}

export async function getCompanyRecommendations(
  companyId: string,
  payload: { problem_statement_id?: string | null; top_k_professors?: number; patents_per_professor?: number } = {},
) {
  return (
    await api.post(`/company-dashboard/${companyId}/recommendations`, payload)
  ).data as CompanyRecommendationsResult;
}

export type ProfessorInteractionType =
  | "view" | "save" | "connect" | "invite_collaborate" | "message" | "licensing_inquiry";

export async function logProfessorInteraction(
  companyId: string,
  payload: { professor_id: string; interaction_type: ProfessorInteractionType; match_score?: number },
) {
  return (
    await api.post(`/company-dashboard/${companyId}/professor-interactions`, payload)
  ).data as { logged: boolean };
}

// ─── Buyer (Professor/Institute) -> Raw Patent Pool ────────────────────────
// Powers the "Discover Patents" panel for a professor/institute buyer -
// ranks every patent on the platform (not just curated marketplace
// listings) against the buyer's own registered profile. Backed by the
// unified /match endpoint (target_kind="patent_pool").

export type PatentBuyerType = "professor" | "institute";

export async function discoverPatentPool(
  buyerType: PatentBuyerType,
  buyerId: string,
  payload: { top_k?: number } = {},
) {
  return runUnifiedMatch({
    source_kind: buyerType, source_id: buyerId, target_kind: "patent_pool", top_k: payload.top_k,
  });
}

// Cross-institute discovery grouped by professor (each carrying their
// affiliated institute) instead of a flat patent list - powers the
// Professor Dashboard's Institute section ("institutes can buy patents from
// professors across different institutes, grouped by professor + institute").
export interface ProfessorPatentGroup {
  professor_id: string;
  professor_name: string;
  department: string;
  institute: string;
  patent_count: number;
  max_score: number;
  average_score: number;
  patents: MatchResult[];
}

export interface DiscoverGroupedResult {
  buyer_type: PatentBuyerType;
  buyer_id: string;
  professor_count: number;
  groups: ProfessorPatentGroup[];
}

export async function discoverPatentsGrouped(
  buyerType: PatentBuyerType,
  buyerId: string,
  payload: { top_k_professors?: number; patents_per_professor?: number } = {},
) {
  return (
    await api.post(`/marketplace/discover/${buyerType}/${buyerId}/grouped`, payload)
  ).data as DiscoverGroupedResult;
}

// ─── Matching Engine 7: Student Dashboard (Student -> Patents & Professors) ─

export interface ParsedResumeSuggestion {
  skills: string[];
  education: string[];
  projects: string[];
  publications: string[];
  certifications: string[];
  internships: string[];
  work_experience: string[];
  research_interests: string[];
  career_goals: string;
  preferred_domains: string[];
  achievements_soft_skills: string[];
  extraction_quality: "ok" | "low_text";
  resume_filename: string;
  resume_text: string;
  resume_file_path: string;
}

export async function uploadResume(file: File, userId?: string) {
  const form = new FormData();
  form.append("file", file);
  if (userId) form.append("user_id", userId);
  return (
    await api.post("/marketplace/profiles/student/resume", form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
  ).data as ParsedResumeSuggestion;
}

export interface StudentPatentMatch {
  listing_id: string;
  patent_title: string;
  professor_id: string;
  professor_name: string;
  department: string;
  technology_domain: string;
  match_score: number;
  semantic_score: number;
  keyword_score: number;
  confidence: "high" | "medium" | "low";
  asking_price_inr: number | null;
  licensing_terms: Record<string, any>;
  status: string;
  commercialization_stage: string;
  industry_applications: string[];
  reasons: string[];
  skill_alignment: string[];
  business_potential: string;
}

export interface StudentProfessorMatch {
  professor_id: string;
  professor_name: string;
  department: string;
  research_areas: string[];
  available_patent_count: number;
  max_score: number;
  average_score: number;
  featured_patents: StudentPatentMatch[];
}

export async function getStudentRecommendations(
  studentId: string,
  payload: { top_k_patents?: number; top_k_professors?: number; patents_per_professor?: number } = {},
) {
  return (
    await api.post(`/student-dashboard/${studentId}/recommendations`, payload)
  ).data as {
    student_id: string;
    recommended_patents: StudentPatentMatch[];
    recommended_professors: StudentProfessorMatch[];
  };
}

export async function getStudentOverview(studentId: string) {
  return (await api.get(`/student-dashboard/${studentId}/overview`)).data as {
    student_id: string;
    profile_completion_pct: number;
    avg_match_score: number;
    recommended_patent_count: number;
    recommended_professor_count: number;
    saved_patents_count: number;
    purchased_licensed_count: number;
    notifications_count: number;
  };
}

export async function getStudentPatentDetail(studentId: string, listingId: string) {
  return (await api.get(`/student-dashboard/${studentId}/patents/${listingId}`)).data as {
    listing: Record<string, any>;
    compatibility: StudentPatentMatch | null;
  };
}

export interface SkillGapAnalysis {
  missing_skills: string[];
  recommended_courses: string[];
  suggested_certifications: string[];
  recommended_papers: string[];
  suggested_projects: string[];
  readiness_score: number;
  source: "claude" | "rule" | "cache";
}

export async function getSkillGapAnalysis(studentId: string, listingId: string) {
  return (await api.get(`/student-dashboard/${studentId}/skill-gap/${listingId}`)).data as SkillGapAnalysis;
}

export interface StartupInsights {
  startup_ideas: string[];
  business_model: string;
  target_industries: string[];
  customer_segments: string[];
  revenue_opportunities: string[];
  commercialization_roadmap: string[];
  market_potential_score: number;
  source: "claude" | "rule" | "cache";
}

export async function getStartupInsights(listingId: string) {
  return (await api.get(`/patents/${listingId}/startup-insights`)).data as StartupInsights;
}

export interface PatentTransaction {
  transaction_id: string;
  listing_id: string;
  patent_title: string;
  professor_id: string;
  professor_name: string;
  buyer_type: string;
  buyer_id: string;
  transaction_type: "purchase" | "license";
  price: number | null;
  license_expiry: number | null;
  status: "pending" | "completed" | "cancelled";
  created_at: number;
}

export async function getPurchasedLicensedPatents(studentId: string) {
  return (
    await api.get(`/student-dashboard/${studentId}/purchased-licensed`)
  ).data as { student_id: string; count: number; transactions: PatentTransaction[] };
}

// ─── Matching Engine 8: Employee Dashboard (Employee -> Patents & Professors) ─
// Structurally identical result shapes to the Student Dashboard's Engine 7 -
// reuse the same TS interfaces via type aliases rather than duplicating them.

export type EmployeePatentMatch = StudentPatentMatch;
export type EmployeeProfessorMatch = StudentProfessorMatch;

export async function uploadEmployeeResume(file: File, userId?: string) {
  const form = new FormData();
  form.append("file", file);
  if (userId) form.append("user_id", userId);
  return (
    await api.post("/marketplace/profiles/employee/resume", form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
  ).data as ParsedResumeSuggestion;
}

export async function getEmployeeRecommendations(
  employeeId: string,
  payload: { top_k_patents?: number; top_k_professors?: number; patents_per_professor?: number } = {},
) {
  return (
    await api.post(`/employee-dashboard/${employeeId}/recommendations`, payload)
  ).data as {
    employee_id: string;
    recommended_patents: EmployeePatentMatch[];
    recommended_professors: EmployeeProfessorMatch[];
  };
}

export async function getEmployeeOverview(employeeId: string) {
  return (await api.get(`/employee-dashboard/${employeeId}/overview`)).data as {
    employee_id: string;
    profile_completion_pct: number;
    avg_match_score: number;
    recommended_patent_count: number;
    recommended_professor_count: number;
    saved_patents_count: number;
    purchased_licensed_count: number;
    notifications_count: number;
  };
}

export async function getEmployeePatentDetail(employeeId: string, listingId: string) {
  return (await api.get(`/employee-dashboard/${employeeId}/patents/${listingId}`)).data as {
    listing: Record<string, any>;
    compatibility: EmployeePatentMatch | null;
  };
}

export async function getEmployeeSkillGapAnalysis(employeeId: string, listingId: string) {
  return (await api.get(`/employee-dashboard/${employeeId}/skill-gap/${listingId}`)).data as SkillGapAnalysis;
}

export async function getEmployeePurchasedLicensedPatents(employeeId: string) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/purchased-licensed`)
  ).data as { employee_id: string; count: number; transactions: PatentTransaction[] };
}

// ─── AI Matching Engine 9 (Student/Employee Dashboard: Job Postings) ──────

export type JobEmploymentType = "full_time" | "internship";

export interface JobPosting {
  job_id: string;
  company_id: string;
  company_name: string;
  title: string;
  description: string;
  required_skills: string[];
  preferred_skills: string[];
  min_experience_years: number;
  education_requirement: string;
  certifications_preferred: string[];
  keywords: string[];
  domain_tags: string[];
  employment_type: JobEmploymentType;
  is_remote: boolean;
  location: string;
  status: string;
  created_at: number;
  updated_at: number;
}

export interface JobMatch {
  job_id: string;
  title: string;
  company_name: string;
  match_score: number;
  semantic_score: number;
  skills_score: number;
  experience_score: number;
  education_score: number;
  certifications_score: number;
  keywords_score: number;
  confidence: "high" | "medium" | "low";
  matching_skills: string[];
  missing_skills: string[];
  reasons: string[];
  employment_type: JobEmploymentType;
  is_remote: boolean;
  location: string;
  created_at: number;
}

export async function getJobMatches(
  studentId: string,
  params: { sort?: "match" | "newest"; employment_type?: JobEmploymentType; is_remote?: boolean } = {},
) {
  return (
    await api.get(`/student-dashboard/${studentId}/job-matches`, { params })
  ).data as { student_id: string; count: number; matches: JobMatch[] };
}

export interface JobMatchSuggestions {
  skills_to_learn: string[];
  resume_suggestions: string[];
  recommended_courses_certs: string[];
  source: "claude" | "rule" | "cache";
}

export async function getJobMatchSuggestions(studentId: string, jobId: string) {
  return (
    await api.get(`/student-dashboard/${studentId}/job-matches/${jobId}/suggestions`)
  ).data as JobMatchSuggestions;
}

export async function applyToJob(studentId: string, jobId: string) {
  return (
    await api.post(`/student-dashboard/${studentId}/job-matches/${jobId}/apply`)
  ).data as { application_id: string; status: string; already_applied: boolean };
}

export interface JobApplication {
  application_id: string;
  student_id: string;
  job_id: string;
  status: string;
  match_score_snapshot: number | null;
  applied_at: number;
}

export async function getStudentApplications(studentId: string) {
  return (
    await api.get(`/student-dashboard/${studentId}/applications`)
  ).data as { student_id: string; count: number; applications: JobApplication[] };
}

// Employee Dashboard mirror - identical shapes (JobMatch/JobMatchSuggestions/
// JobApplication), just a different URL prefix, same as how the patent-
// matching Engine 7 functions above are mirrored for employees.

export async function getEmployeeJobMatches(
  employeeId: string,
  params: { sort?: "match" | "newest"; employment_type?: JobEmploymentType; is_remote?: boolean } = {},
) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/job-matches`, { params })
  ).data as { employee_id: string; count: number; matches: JobMatch[] };
}

export async function getEmployeeJobMatchSuggestions(employeeId: string, jobId: string) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/job-matches/${jobId}/suggestions`)
  ).data as JobMatchSuggestions;
}

export async function applyToJobAsEmployee(employeeId: string, jobId: string) {
  return (
    await api.post(`/employee-dashboard/${employeeId}/job-matches/${jobId}/apply`)
  ).data as { application_id: string; status: string; already_applied: boolean };
}

export async function getEmployeeApplications(employeeId: string) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/applications`)
  ).data as { employee_id: string; count: number; applications: JobApplication[] };
}

// ─── AI Matching Engine 8: Research Opportunities (Student <-> Professor) ──
// Student Dashboard's "AI Matching Engine 8" tab (ranked opportunities,
// mirroring the Job Matching Engine above) + the Professor Dashboard's
// ranked-candidate-students section (a new direction the job engine never
// needed) + resume preview/download.

export type OpportunityType =
  | "research_internship" | "masters" | "phd" | "postdoctoral" | "research_assistant"
  | "thesis_dissertation" | "lab_position" | "collaborative_project"
  | "visiting_researcher" | "fellowship" | "summer_winter_program" | "other";

export const OPPORTUNITY_TYPE_LABELS: Record<OpportunityType, string> = {
  research_internship: "Research Internship",
  masters: "Master's Position",
  phd: "PhD Position",
  postdoctoral: "Postdoctoral",
  research_assistant: "Research Assistant",
  thesis_dissertation: "Thesis/Dissertation",
  lab_position: "Lab Position",
  collaborative_project: "Collaborative Project",
  visiting_researcher: "Visiting Researcher",
  fellowship: "Fellowship",
  summer_winter_program: "Summer/Winter Program",
  other: "Other",
};

export interface ResearchOpportunity {
  opportunity_id: string;
  professor_id: string;
  professor_name: string;
  department: string;
  title: string;
  description: string;
  opportunity_type: OpportunityType;
  degree_level: string;
  research_areas: string[];
  required_skills: string[];
  preferred_skills: string[];
  required_qualifications: string[];
  preferred_qualifications: string[];
  min_experience_years: number;
  education_requirement: string;
  publications_expected: boolean;
  keywords: string[];
  domain_tags: string[];
  duration: string;
  stipend_or_funding: string;
  location: string;
  is_remote: boolean;
  university: string;
  status: string;
  created_at: number;
  updated_at: number;
}

export interface OpportunityMatch {
  opportunity_id: string;
  title: string;
  professor_name: string;
  department: string;
  opportunity_type: OpportunityType;
  degree_level: string;
  match_score: number;
  semantic_score: number;
  skills_score: number;
  research_fit_score: number;
  experience_score: number;
  qualifications_score: number;
  keywords_score: number;
  confidence: "high" | "medium" | "low";
  matching_skills: string[];
  missing_skills: string[];
  reasons: string[];
  duration: string;
  location: string;
  is_remote: boolean;
  created_at: number;
}

export interface OpportunitySuggestions {
  skills_to_learn: string[];
  resume_suggestions: string[];
  recommended_courses_certs: string[];
  source: "claude" | "rule" | "cache";
}

export interface FitExplanation {
  summary: string;
  key_strengths: string[];
  potential_gaps: string[];
  source: "claude" | "rule" | "cache";
}

export interface OpportunityInsights {
  opportunity_id: string;
  top_candidates: (OpportunityMatch & { student_id: string })[];
  near_miss_students: (OpportunityMatch & { student_id: string })[];
  strength_summaries: Record<string, string>;
  suggested_keyword_updates: string[];
}

export interface OpportunityCandidate extends OpportunityMatch {
  student_id: string;
  student_name: string;
  institute: string;
  field_of_study: string;
  skills: string[];
  education: string[];
  bio: string;
  resume_file_path: string;
}

export interface OpportunityInvitation {
  invitation_id: string;
  opportunity_id: string;
  opportunity_title: string;
  professor_id: string;
  professor_name: string;
  student_id: string;
  student_name: string;
  match_score: number | null;
  score_breakdown: Record<string, any>;
  reasons: string[];
  message: string;
  status: "sent" | "viewed" | "accepted" | "declined";
  created_at: number;
  responded_at: number | null;
}

export interface OpportunityInterest {
  interest_id: string;
  student_id: string;
  opportunity_id: string;
  status: string;
  match_score_snapshot: number | null;
  message: string;
  expressed_at: number;
}

// Student-facing

export async function getOpportunityMatches(
  studentId: string,
  params: { sort?: "match" | "newest"; opportunity_type?: OpportunityType; degree_level?: string } = {},
) {
  return (
    await api.get(`/student-dashboard/${studentId}/opportunity-matches`, { params })
  ).data as { student_id: string; count: number; matches: OpportunityMatch[] };
}

export async function getOpportunityMatchSuggestions(studentId: string, opportunityId: string) {
  return (
    await api.get(`/student-dashboard/${studentId}/opportunity-matches/${opportunityId}/suggestions`)
  ).data as OpportunitySuggestions;
}

export async function getOpportunityFitExplanation(studentId: string, opportunityId: string) {
  return (
    await api.get(`/student-dashboard/${studentId}/opportunity-matches/${opportunityId}/fit-explanation`)
  ).data as FitExplanation;
}

export async function expressInterest(studentId: string, opportunityId: string, message = "") {
  return (
    await api.post(`/student-dashboard/${studentId}/opportunity-matches/${opportunityId}/express-interest`, null, {
      params: { message },
    })
  ).data as { interest_id: string; status: string; already_interested: boolean };
}

export async function getStudentInterests(studentId: string) {
  return (
    await api.get(`/student-dashboard/${studentId}/opportunity-interests`)
  ).data as { student_id: string; count: number; interests: OpportunityInterest[] };
}

export async function getStudentInvitationsReceived(studentId: string) {
  return (
    await api.get(`/student-dashboard/${studentId}/invitations-received`)
  ).data as { student_id: string; count: number; invitations: OpportunityInvitation[] };
}

export function getResumeDownloadUrl(studentId: string): string {
  return `${API_BASE}/marketplace/profiles/student/${encodeURIComponent(studentId)}/resume/download`;
}

// Opportunity CRUD (Professor Dashboard's "Post a Research Opportunity" form)

export async function createResearchOpportunity(payload: Omit<ResearchOpportunity, "opportunity_id" | "status" | "created_at" | "updated_at">) {
  return (await api.post("/research-opportunities", payload)).data as { opportunity_id: string; saved: boolean };
}

export async function updateResearchOpportunity(opportunityId: string, payload: Partial<ResearchOpportunity>) {
  return (await api.patch(`/research-opportunities/${opportunityId}`, payload)).data as ResearchOpportunity;
}

export async function closeResearchOpportunity(opportunityId: string) {
  return (await api.delete(`/research-opportunities/${opportunityId}`)).data as ResearchOpportunity;
}

export async function listResearchOpportunities(params: {
  status?: string; opportunity_type?: OpportunityType; degree_level?: string; professor_id?: string;
} = {}) {
  return (await api.get("/research-opportunities", { params })).data as { count: number; opportunities: ResearchOpportunity[] };
}

export async function getProfessorResearchOpportunities(professorId: string, status?: string) {
  return (
    await api.get(`/professor/${professorId}/research-opportunities`, { params: { status } })
  ).data as { professor_id: string; count: number; opportunities: ResearchOpportunity[] };
}

// Professor-facing: ranked candidates, insights, invite

export async function getOpportunityCandidates(
  professorId: string,
  opportunityId: string,
  filters: {
    min_match_score?: number; degree_level?: string; research_area?: string;
    skill?: string; university?: string; location?: string;
  } = {},
) {
  return (
    await api.get(`/professor/${professorId}/research-opportunities/${opportunityId}/candidates`, { params: filters })
  ).data as { opportunity_id: string; count: number; candidates: OpportunityCandidate[] };
}

export async function getOpportunityInsights(professorId: string, opportunityId: string) {
  return (
    await api.get(`/professor/${professorId}/research-opportunities/${opportunityId}/insights`)
  ).data as OpportunityInsights;
}

export async function inviteStudent(professorId: string, opportunityId: string, studentId: string, message = "") {
  return (
    await api.post(`/professor/${professorId}/research-opportunities/${opportunityId}/invite`, {
      student_id: studentId, message,
    })
  ).data as { invitation_id: string; status: string };
}

export async function getProfessorInvitationsSent(professorId: string) {
  return (
    await api.get(`/professor/${professorId}/invitations-sent`)
  ).data as { professor_id: string; count: number; invitations: OpportunityInvitation[] };
}

// Employee Dashboard mirror - identical shapes, same reuse pattern as the
// Job Opportunities (Matching Engine 9) employee mirror above.

export async function getEmployeeOpportunityMatches(
  employeeId: string,
  params: { sort?: "match" | "newest"; opportunity_type?: OpportunityType; degree_level?: string } = {},
) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/opportunity-matches`, { params })
  ).data as { employee_id: string; count: number; matches: OpportunityMatch[] };
}

export async function getEmployeeOpportunityMatchSuggestions(employeeId: string, opportunityId: string) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/opportunity-matches/${opportunityId}/suggestions`)
  ).data as OpportunitySuggestions;
}

export async function getEmployeeOpportunityFitExplanation(employeeId: string, opportunityId: string) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/opportunity-matches/${opportunityId}/fit-explanation`)
  ).data as FitExplanation;
}

export async function expressInterestAsEmployee(employeeId: string, opportunityId: string, message = "") {
  return (
    await api.post(`/employee-dashboard/${employeeId}/opportunity-matches/${opportunityId}/express-interest`, null, {
      params: { message },
    })
  ).data as { interest_id: string; status: string; already_interested: boolean };
}

export async function getEmployeeOpportunityInterests(employeeId: string) {
  return (
    await api.get(`/employee-dashboard/${employeeId}/opportunity-interests`)
  ).data as { employee_id: string; count: number; interests: OpportunityInterest[] };
}
