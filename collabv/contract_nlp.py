"""
CollabV AI - Model 7: Contract & MoU NLP Parser
==================================================
Three capabilities:
  1. parse(text)    -> ContractTerms : extract structured terms from any
                                       collaboration agreement text.
  2. compare(a, b)  -> ContractDiff  : diff two parsed contracts and flag
                                       significant changes.
  3. generate_template(...)         : produce a filled MoU/agreement template
                                       for one of 5 standard collaboration types.

The parser is two-tier:
  Tier 1: regex/rule-based extraction (always runs)
  Tier 2: Claude API extraction for complex clauses (runs if API key present)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class Party:
    name: str = ""
    role: str = ""             # "company" | "institution" | "professor"
    address: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Milestone:
    description: str = ""
    target_date: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CollaborationScope:
    research_area: str = ""
    objectives: List[str] = field(default_factory=list)
    deliverables: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Timeline:
    start_date: str = ""
    end_date: str = ""
    duration_months: int = 0
    milestones: List[Milestone] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["milestones"] = [m.to_dict() if isinstance(m, Milestone) else m for m in self.milestones]
        return d


@dataclass
class FinancialTerms:
    total_amount: float = 0.0
    currency: str = "INR"
    payment_schedule: str = ""
    funding_source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IPTerms:
    ownership_split: str = ""             # e.g. "50/50 joint", "company-owned"
    licensing_rights: str = ""
    publication_rights: str = ""
    background_ip: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConfidentialityTerms:
    nda_duration_months: int = 0
    scope: str = ""
    exceptions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TerminationTerms:
    notice_period_days: int = 0
    conditions: List[str] = field(default_factory=list)
    exit_obligations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Obligation:
    party: str = ""
    description: str = ""
    deadline: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContractTerms:
    parties: List[Party] = field(default_factory=list)
    scope: CollaborationScope = field(default_factory=CollaborationScope)
    timeline: Timeline = field(default_factory=Timeline)
    financial: FinancialTerms = field(default_factory=FinancialTerms)
    ip_terms: IPTerms = field(default_factory=IPTerms)
    confidentiality: ConfidentialityTerms = field(default_factory=ConfidentialityTerms)
    termination: TerminationTerms = field(default_factory=TerminationTerms)
    obligations: List[Obligation] = field(default_factory=list)
    governing_law: str = ""
    special_clauses: List[str] = field(default_factory=list)
    needs_review: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parties": [p.to_dict() for p in self.parties],
            "scope": self.scope.to_dict(),
            "timeline": self.timeline.to_dict(),
            "financial": self.financial.to_dict(),
            "ip_terms": self.ip_terms.to_dict(),
            "confidentiality": self.confidentiality.to_dict(),
            "termination": self.termination.to_dict(),
            "obligations": [o.to_dict() for o in self.obligations],
            "governing_law": self.governing_law,
            "special_clauses": self.special_clauses,
            "needs_review": self.needs_review,
        }


@dataclass
class FieldDiff:
    field_name: str
    value_a: Any
    value_b: Any
    significance: str       # "high" | "medium" | "low"


@dataclass
class ContractDiff:
    changed_fields: List[FieldDiff] = field(default_factory=list)
    risk_assessment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "changed_fields": [asdict(f) for f in self.changed_fields],
            "risk_assessment": self.risk_assessment,
        }


# ─── Built-in templates ─────────────────────────────────────────────────────

_BASE_HEADER = """MEMORANDUM OF UNDERSTANDING

This Memorandum of Understanding ("MoU") is entered into on {{start_date}} between:

(1) {{company_name}}, a company incorporated under the laws of India,
    having its registered office at {{company_address}} (hereinafter "Company"); and

(2) Indian Institute of Technology Madras, an autonomous Institute of National
    Importance established under the Institutes of Technology Act, 1961, with
    address at IIT P.O., Chennai - 600 036, Tamil Nadu, India (hereinafter
    "Institute"), represented through its faculty member Dr. {{professor_name}},
    {{department}}.

The Company and the Institute are hereinafter individually referred to as a
"Party" and collectively as the "Parties".
"""

_BASE_FOOTER = """13. GOVERNING LAW AND JURISDICTION

This Agreement shall be governed by and construed in accordance with the laws
of India. The courts at Chennai, Tamil Nadu shall have exclusive jurisdiction
over any dispute arising under this Agreement.

14. ENTIRE AGREEMENT

This Agreement constitutes the entire understanding between the Parties and
supersedes all prior negotiations, representations, or agreements relating to
the subject matter hereof.

IN WITNESS WHEREOF, the Parties have executed this Agreement on the date first
written above.

For {{company_name}}              For Indian Institute of Technology Madras

____________________               ____________________
Name:                              Dr. {{professor_name}}
Title:                             {{department}}
Date:                              Date:
"""


TEMPLATES: Dict[str, str] = {
    "advisory": _BASE_HEADER + """
1. PURPOSE

The Institute, through Dr. {{professor_name}}, shall provide expert advisory
services to the Company in the area of {{research_area}}. The engagement is
limited to strategic guidance and shall not involve laboratory experimentation
or extended research activities.

2. SCOPE OF SERVICES

The Professor shall:
(a) Provide up to {{hours_per_month}} hours of advisory support per month;
(b) Participate in technical review meetings as mutually agreed;
(c) Review documents, designs, and technical proposals submitted by the Company;
(d) Recommend strategic technical directions in {{research_area}}.

3. TERM

This Agreement shall commence on {{start_date}} and continue until {{end_date}},
unless terminated earlier in accordance with Clause 9.

4. COMPENSATION

The Company shall pay the Institute a consolidated advisory fee of INR
{{amount}} for the entire term, payable as follows:
  - 25% on signing of this Agreement;
  - 50% in equal quarterly instalments during the term;
  - 25% on satisfactory completion of the advisory engagement.

5. INTELLECTUAL PROPERTY

(a) Background IP belonging to either Party prior to this Agreement shall remain
    the sole property of that Party.
(b) Foreground IP arising solely from the Professor's advice shall remain with
    the Institute, subject to a royalty-free, non-exclusive licence in favour
    of the Company for internal evaluation purposes.
(c) IP created solely by Company personnel shall belong to the Company.

6. PUBLICATIONS

The Professor retains the right to publish academic work, subject to a 30-day
review by the Company to remove confidential information, with no right to
block publication.

7. CONFIDENTIALITY

Each Party shall protect Confidential Information disclosed by the other for a
period of three (3) years from the date of disclosure. Standard exceptions
apply (publicly available information, independently developed information,
information received from a third party without obligations of confidence).

8. NO EMPLOYMENT RELATIONSHIP

Nothing in this Agreement creates an employer-employee relationship between the
Company and the Professor or the Institute.

9. TERMINATION

Either Party may terminate this Agreement by providing 30 days' written notice.
On termination, the Company shall pay for advisory services rendered up to the
effective date of termination.

""" + _BASE_FOOTER,

    "joint_research": _BASE_HEADER + """
1. PURPOSE

The Parties shall undertake a joint research collaboration in the field of
{{research_area}} with the objective of {{objective}}.

2. SCOPE OF RESEARCH

(a) Both Parties shall contribute personnel, expertise, and resources to the
    joint research programme.
(b) The Institute shall provide laboratory facilities, faculty supervision
    through Dr. {{professor_name}}, and graduate student support.
(c) The Company shall provide industrial datasets, domain expertise, and may
    deploy engineers on-site at the Institute.

3. DELIVERABLES

The joint research programme shall produce:
  - Technical reports at the end of each project milestone;
  - A final research report at the conclusion of the project;
  - Patent applications for novel inventions, jointly filed.

4. TERM

This Agreement shall commence on {{start_date}} and continue until {{end_date}},
a duration of {{duration_months}} months, with possibility of extension by
mutual written agreement.

5. PROJECT BUDGET

The total project budget is INR {{amount}}, with the Company contributing the
full amount as a research grant to the Institute, disbursed in three tranches:
  - 40% on signing of this Agreement;
  - 40% on completion of the mid-term milestone;
  - 20% on submission of the final report.

6. INTELLECTUAL PROPERTY

(a) Background IP of each Party remains with that Party.
(b) Foreground IP created jointly during the programme shall be jointly owned by
    the Institute and the Company in equal shares.
(c) The Company shall have a first right of negotiation for an exclusive
    commercial licence to jointly-owned IP, on commercially reasonable terms.
(d) IP created solely by Institute personnel using Institute resources shall be
    owned by the Institute but licensed royalty-free to the Company for
    research purposes.

7. PUBLICATIONS

The Institute retains academic publication rights. The Company shall have a
60-day prior review window to request removal of confidential information and
a 90-day filing window to file patent applications.

8. CONFIDENTIALITY

Each Party shall protect Confidential Information for a period of five (5)
years from the date of disclosure. Standard exceptions apply.

9. PERSONNEL

Each Party's personnel remain employees of that Party. No party shall make
representations binding on the other.

10. TERMINATION

Either Party may terminate this Agreement for material breach with 60 days'
written notice and an opportunity to cure. On termination, jointly-owned IP
shall be governed by an interim co-ownership agreement pending wind-down.

""" + _BASE_FOOTER,

    "consulting": _BASE_HEADER + """
1. PURPOSE

The Professor shall provide paid consulting services to the Company in the area
of {{research_area}}, on a project basis.

2. SCOPE OF CONSULTING

The Professor shall:
(a) Deliver specific consulting outputs as set out in Annexure A;
(b) Attend up to {{hours_per_month}} hours per month of engagement;
(c) Produce written technical recommendations within the scope of engagement.

3. TERM

This Agreement is effective from {{start_date}} to {{end_date}}.

4. CONSULTING FEE

The Company shall pay a consulting fee of INR {{amount}} for the entire
engagement, inclusive of all taxes except GST which shall be additional.

5. INTELLECTUAL PROPERTY

(a) All IP created by the Professor specifically in the course of providing
    services under this Agreement, and arising directly from the work
    commissioned by the Company, shall vest with the Company, subject to:
      (i)  the Professor retaining all moral rights;
      (ii) prior background IP of the Professor or the Institute being
           excluded.
(b) The Professor shall not be required to assign any IP that arises
    independently of the consulting engagement.

6. PUBLICATIONS

Consulting outputs delivered to the Company are confidential and may not be
published without prior written consent of the Company. General methods and
techniques used by the Professor remain available for academic use.

7. CONFIDENTIALITY

The Professor shall protect Confidential Information disclosed by the Company
for a period of three (3) years.

8. INSTITUTE NOTIFICATION

The Professor confirms that this consulting engagement is undertaken in
compliance with the Institute's consulting policy and any applicable mandatory
disclosures have been made.

9. TERMINATION

Either Party may terminate this Agreement with 30 days' written notice. The
Company shall pay for services rendered up to the date of termination.

""" + _BASE_FOOTER,

    "sponsored_research": _BASE_HEADER + """
1. PURPOSE

The Company sponsors a research programme in {{research_area}} to be conducted
by Dr. {{professor_name}} at the Institute, with the Company having the first
right to commercialise resulting technologies.

2. SCOPE OF RESEARCH

(a) The Institute shall conduct independent research in {{research_area}}.
(b) The Company may provide problem statements, datasets, and review milestones
    but shall not direct the day-to-day research activities.
(c) Research personnel shall be hired and supervised by the Institute.

3. TERM AND DURATION

This Agreement is effective from {{start_date}} for a period of
{{duration_months}} months ending on {{end_date}}, with the option to extend
by mutual written agreement.

4. SPONSORSHIP AMOUNT

The Company shall sponsor the research with INR {{amount}}, paid as follows:
  - 30% on execution of this Agreement;
  - 30% on completion of mid-term milestone;
  - 30% on completion of the second milestone;
  - 10% on submission of the final report.

5. INTELLECTUAL PROPERTY

(a) All IP arising from this sponsored research shall be owned by the Institute.
(b) The Company shall have a first right of negotiation for a royalty-bearing,
    exclusive, worldwide licence to commercialise the IP, exercisable within
    180 days of disclosure of the invention.
(c) If the Company does not exercise its first right within the period, the
    Institute is free to license the IP to third parties.
(d) Royalty terms shall be negotiated in good faith based on industry norms.

6. PUBLICATIONS

The Institute and the Professor retain full publication rights. The Company has
a 60-day prior review window to (i) request removal of confidential
information and (ii) file patent applications.

7. CONFIDENTIALITY

Each Party shall protect Confidential Information for a period of five (5)
years. Research results published with prior review shall not be considered
confidential after publication.

8. REPORTING

The Institute shall provide quarterly progress reports and one final technical
report.

9. TERMINATION

The Company may terminate the Agreement for material breach with 60 days'
written notice and an opportunity to cure. On termination, the Institute shall
retain funds paid up to the effective date and complete work in progress where
practicable.

""" + _BASE_FOOTER,

    "technology_licensing": _BASE_HEADER + """
1. PURPOSE

The Institute hereby grants, and the Company hereby accepts, a licence to
practise the Licensed Technology developed by Dr. {{professor_name}} in the
area of {{research_area}}, on the terms set out below.

2. LICENSED TECHNOLOGY

The Licensed Technology comprises:
  - Patent application(s) and any patents that may issue therefrom, as listed
    in Annexure A;
  - Associated know-how and technical documentation reasonably necessary for
    the Company to practise the technology.

3. GRANT OF LICENCE

(a) The Institute grants the Company a {{exclusivity}} (exclusive / non-exclusive)
    worldwide licence to make, use, sell, and import products embodying the
    Licensed Technology in the Field of {{field_of_use}}.
(b) The Institute retains the right to use the Licensed Technology for academic
    research and teaching.

4. TERM

This Agreement is effective from {{start_date}} and shall continue for the
life of the last-to-expire licensed patent, unless earlier terminated.

5. CONSIDERATION

(a) Upfront fee: INR {{amount}} payable within 30 days of the Effective Date.
(b) Royalties: {{royalty_percent}}% of Net Sales of Licensed Products, payable
    quarterly within 45 days of the end of each calendar quarter.
(c) Minimum annual royalty: INR {{minimum_royalty}} commencing in Year 2.

6. PATENT PROSECUTION AND MAINTENANCE

The Institute shall continue to prosecute and maintain the licensed patents.
The Company shall reimburse the Institute for all reasonable patent
prosecution and maintenance costs incurred after the Effective Date.

7. IMPROVEMENTS

Improvements made by the Institute during the term shall be offered to the
Company on the same licence terms. Improvements made by the Company shall be
owned by the Company, subject to a non-exclusive licence back to the Institute
for academic research.

8. CONFIDENTIALITY

Each Party shall protect Confidential Information for a period of five (5)
years.

9. WARRANTIES

The Institute makes no warranties regarding the validity of the Licensed
Patents or non-infringement of third-party rights. The Licensed Technology is
provided "AS IS".

10. TERMINATION

(a) The Institute may terminate for non-payment of royalties or material breach
    with 60 days' written notice and cure opportunity.
(b) The Company may terminate for convenience with 180 days' written notice.

""" + _BASE_FOOTER,
}


# ─── Regex patterns ─────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(
    r"(?:INR|Rs\.?|Rupees|USD|\$|₹)\s*([\d,]+(?:\.\d+)?)(?:\s*(crore|cr|lakhs?|million|mn|m|thousand|k))?",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_DURATION_RE = re.compile(r"(\d+)\s*(months?|years?|days?)", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})"
    r"|((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s*\d{2,4})"
    r"|(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_NDA_RE = re.compile(
    r"(?:confidential|nda|non-disclosure)[^.]{0,80}?(\d+)\s*(year|month|day)s?",
    re.IGNORECASE,
)
_NOTICE_RE = re.compile(
    r"(\d+)\s*days?[^.]{0,80}?(?:written\s+)?notice",
    re.IGNORECASE,
)


# ─── Parser ─────────────────────────────────────────────────────────────────

class ContractParser:
    """Parse, compare, and generate collaboration agreements."""

    def __init__(self, use_claude: bool = True, api_key: Optional[str] = None) -> None:
        self.use_claude = use_claude
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    # ─── Public ─────────────────────────────────────────────────────────────

    def parse(self, text: str) -> ContractTerms:
        """Extract structured terms from a collaboration agreement."""
        terms = ContractTerms()
        if not text or not text.strip():
            terms.needs_review.append("Empty document")
            return terms

        # Tier 1: rule-based
        self._extract_amounts(text, terms)
        self._extract_dates(text, terms)
        self._extract_duration(text, terms)
        self._extract_parties(text, terms)
        self._extract_nda(text, terms)
        self._extract_notice_period(text, terms)
        self._extract_governing_law(text, terms)
        self._extract_ip_terms(text, terms)
        self._extract_scope(text, terms)

        # Tier 2: Claude for complex clauses (if available)
        if self.use_claude and self.api_key:
            try:
                self._enrich_with_claude(text, terms)
            except Exception as e:
                logger.warning("Claude enrichment failed: %s", e)
                terms.needs_review.append("Claude enrichment unavailable")

        # Flag low-coverage fields
        if not terms.financial.total_amount:
            terms.needs_review.append("Total amount could not be extracted")
        if not terms.timeline.start_date:
            terms.needs_review.append("Start date could not be extracted")
        if not terms.parties:
            terms.needs_review.append("Parties could not be identified")

        return terms

    def compare(self, terms_a: ContractTerms, terms_b: ContractTerms) -> ContractDiff:
        """Diff two parsed contracts and assess risk of the change."""
        diffs: List[FieldDiff] = []

        # Compare scalar financial / timeline / IP fields
        scalar_checks = [
            ("financial.total_amount", terms_a.financial.total_amount, terms_b.financial.total_amount, "high"),
            ("financial.currency", terms_a.financial.currency, terms_b.financial.currency, "medium"),
            ("financial.payment_schedule", terms_a.financial.payment_schedule, terms_b.financial.payment_schedule, "medium"),
            ("timeline.start_date", terms_a.timeline.start_date, terms_b.timeline.start_date, "medium"),
            ("timeline.end_date", terms_a.timeline.end_date, terms_b.timeline.end_date, "medium"),
            ("timeline.duration_months", terms_a.timeline.duration_months, terms_b.timeline.duration_months, "high"),
            ("ip_terms.ownership_split", terms_a.ip_terms.ownership_split, terms_b.ip_terms.ownership_split, "high"),
            ("ip_terms.licensing_rights", terms_a.ip_terms.licensing_rights, terms_b.ip_terms.licensing_rights, "high"),
            ("ip_terms.publication_rights", terms_a.ip_terms.publication_rights, terms_b.ip_terms.publication_rights, "medium"),
            ("confidentiality.nda_duration_months", terms_a.confidentiality.nda_duration_months, terms_b.confidentiality.nda_duration_months, "medium"),
            ("termination.notice_period_days", terms_a.termination.notice_period_days, terms_b.termination.notice_period_days, "medium"),
            ("governing_law", terms_a.governing_law, terms_b.governing_law, "low"),
        ]
        for name, a, b, sig in scalar_checks:
            if a != b:
                diffs.append(FieldDiff(field_name=name, value_a=a, value_b=b, significance=sig))

        # Compare scope objectives count
        if len(terms_a.scope.objectives) != len(terms_b.scope.objectives):
            diffs.append(FieldDiff(
                field_name="scope.objectives.count",
                value_a=len(terms_a.scope.objectives),
                value_b=len(terms_b.scope.objectives),
                significance="medium",
            ))

        # Parties
        if [p.name for p in terms_a.parties] != [p.name for p in terms_b.parties]:
            diffs.append(FieldDiff(
                field_name="parties",
                value_a=[p.name for p in terms_a.parties],
                value_b=[p.name for p in terms_b.parties],
                significance="high",
            ))

        # Risk narrative
        high_count = sum(1 for d in diffs if d.significance == "high")
        if high_count >= 2:
            risk = "Multiple high-significance changes - requires legal review."
        elif high_count == 1:
            risk = "One high-significance change - flag to deal owner."
        elif diffs:
            risk = "Minor changes - low risk."
        else:
            risk = "No meaningful changes."

        # Check for one-sided changes (favouring company)
        if terms_a.financial.total_amount and terms_b.financial.total_amount:
            change = terms_b.financial.total_amount - terms_a.financial.total_amount
            if abs(change) >= 0.10 * terms_a.financial.total_amount:
                direction = "increased" if change > 0 else "decreased"
                risk += f" Total amount {direction} by {abs(change):,.0f}."

        return ContractDiff(changed_fields=diffs, risk_assessment=risk)

    def generate_template(
        self,
        collab_type: str,
        company_name: str,
        professor_name: str,
        department: str = "",
        research_area: str = "",
        amount: float = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Fill a built-in template with the provided parameters."""
        key = collab_type.lower().replace(" ", "_").replace("-", "_")
        if key not in TEMPLATES:
            raise ValueError(
                f"Unknown template type '{collab_type}'. "
                f"Available: {', '.join(TEMPLATES.keys())}"
            )

        today = datetime.now().date()
        start = start_date or today.isoformat()
        try:
            start_dt = datetime.fromisoformat(start)
        except Exception:
            start_dt = datetime.now()
        end = end_date or (start_dt + timedelta(days=365)).date().isoformat()
        try:
            end_dt = datetime.fromisoformat(end)
            duration_months = max(1, int((end_dt - start_dt).days / 30))
        except Exception:
            duration_months = 12

        params = {
            "company_name": company_name,
            "company_address": kwargs.get("company_address", "[Company Address]"),
            "professor_name": professor_name,
            "department": department or "[Department]",
            "research_area": research_area or "[Research Area]",
            "amount": f"{amount:,.0f}" if amount else "[Amount]",
            "start_date": start,
            "end_date": end,
            "duration_months": str(duration_months),
            "objective": kwargs.get("objective", "[Project Objective]"),
            "hours_per_month": str(kwargs.get("hours_per_month", 8)),
            "exclusivity": kwargs.get("exclusivity", "non-exclusive"),
            "field_of_use": kwargs.get("field_of_use", "[Field of Use]"),
            "royalty_percent": str(kwargs.get("royalty_percent", 5)),
            "minimum_royalty": f"{kwargs.get('minimum_royalty', 100000):,.0f}",
        }

        out = TEMPLATES[key]
        for k, v in params.items():
            out = out.replace("{{" + k + "}}", str(v))
        return out

    @staticmethod
    def list_templates() -> List[Dict[str, str]]:
        return [
            {"type": "advisory", "name": "Advisory Agreement", "use_case": "Light expert guidance from professor"},
            {"type": "joint_research", "name": "Joint Research Agreement", "use_case": "Shared research, shared IP"},
            {"type": "consulting", "name": "Consulting Agreement", "use_case": "Paid consulting, company owns IP"},
            {"type": "sponsored_research", "name": "Sponsored Research Agreement", "use_case": "Company funds research, first right to license"},
            {"type": "technology_licensing", "name": "Technology Licensing Agreement", "use_case": "Company licenses existing IP"},
        ]

    # ─── Tier 1: Rule-based extractors ──────────────────────────────────────

    @staticmethod
    def _extract_amounts(text: str, terms: ContractTerms) -> None:
        for match in _AMOUNT_RE.finditer(text):
            raw_amount = match.group(1).replace(",", "")
            try:
                value = float(raw_amount)
            except ValueError:
                continue
            multiplier_text = (match.group(2) or "").lower()
            if "crore" in multiplier_text or "cr" == multiplier_text:
                value *= 10_000_000
            elif "lakh" in multiplier_text:
                value *= 100_000
            elif "million" in multiplier_text or multiplier_text in ("mn", "m"):
                value *= 1_000_000
            elif "thousand" in multiplier_text or multiplier_text == "k":
                value *= 1_000

            if value > terms.financial.total_amount:
                terms.financial.total_amount = value
                head = match.group(0)
                if "₹" in head or "INR" in head.upper() or "Rs" in head:
                    terms.financial.currency = "INR"
                elif "$" in head or "USD" in head.upper():
                    terms.financial.currency = "USD"

    @staticmethod
    def _extract_dates(text: str, terms: ContractTerms) -> None:
        dates = []
        for match in _DATE_RE.finditer(text):
            raw = match.group(0)
            dates.append(raw)
        if dates:
            terms.timeline.start_date = dates[0]
            if len(dates) >= 2:
                terms.timeline.end_date = dates[1]

    @staticmethod
    def _extract_duration(text: str, terms: ContractTerms) -> None:
        # Search for "X months" or "Y year" in the proximity of agreement/term keywords
        m = _DURATION_RE.search(text)
        if not m:
            return
        n = int(m.group(1))
        unit = m.group(2).lower()
        if "year" in unit:
            terms.timeline.duration_months = n * 12
        elif "month" in unit:
            terms.timeline.duration_months = n

    @staticmethod
    def _extract_parties(text: str, terms: ContractTerms) -> None:
        # Look for "between X and Y" pattern
        between_match = re.search(
            r"between\s+([^,]+?)\s*(?:,|having|with).*?and\s+([^,]+?)\s*(?:,|having|with|\()",
            text, re.IGNORECASE | re.DOTALL,
        )
        if between_match:
            party_a = between_match.group(1).strip()
            party_b = between_match.group(2).strip()
            terms.parties.append(Party(name=party_a, role="company"))
            terms.parties.append(Party(name=party_b, role="institution"))
            return

        # Fallback: look for "IIT Madras" and any organisation patterns
        if "IIT Madras" in text or "Indian Institute of Technology" in text:
            terms.parties.append(Party(name="Indian Institute of Technology Madras", role="institution"))

    @staticmethod
    def _extract_nda(text: str, terms: ContractTerms) -> None:
        m = _NDA_RE.search(text)
        if not m:
            return
        n = int(m.group(1))
        unit = m.group(2).lower()
        if "year" in unit:
            terms.confidentiality.nda_duration_months = n * 12
        elif "month" in unit:
            terms.confidentiality.nda_duration_months = n

    @staticmethod
    def _extract_notice_period(text: str, terms: ContractTerms) -> None:
        m = _NOTICE_RE.search(text)
        if m:
            terms.termination.notice_period_days = int(m.group(1))

    @staticmethod
    def _extract_governing_law(text: str, terms: ContractTerms) -> None:
        if re.search(r"laws?\s+of\s+India", text, re.IGNORECASE):
            terms.governing_law = "India"
        elif re.search(r"laws?\s+of\s+(?:the\s+)?(\w+)", text, re.IGNORECASE):
            m = re.search(r"laws?\s+of\s+(?:the\s+)?(\w+)", text, re.IGNORECASE)
            if m:
                terms.governing_law = m.group(1)

    @staticmethod
    def _extract_ip_terms(text: str, terms: ContractTerms) -> None:
        lt = text.lower()
        if "jointly owned" in lt or "joint ownership" in lt or "50/50" in lt:
            terms.ip_terms.ownership_split = "Jointly owned"
        elif "shall vest with the company" in lt or "vest in the company" in lt or "owned by the company" in lt:
            terms.ip_terms.ownership_split = "Company-owned"
        elif "owned by the institute" in lt or "vest with the institute" in lt:
            terms.ip_terms.ownership_split = "Institute-owned"

        if "exclusive licence" in lt or "exclusive license" in lt:
            terms.ip_terms.licensing_rights = "Exclusive licence available"
        elif "non-exclusive" in lt:
            terms.ip_terms.licensing_rights = "Non-exclusive licence"
        elif "first right" in lt:
            terms.ip_terms.licensing_rights = "First right of negotiation"

        if "publication" in lt:
            pub_match = re.search(
                r"(\d+)[-\s]days?\s+(?:prior\s+)?review.*?publication",
                lt,
            )
            if pub_match:
                terms.ip_terms.publication_rights = f"{pub_match.group(1)}-day prior review for publications"

    @staticmethod
    def _extract_scope(text: str, terms: ContractTerms) -> None:
        # Look for "in the area of X" / "field of X"
        m = re.search(r"(?:area|field) of ([A-Z][\w\s,&-]{3,80}?)[\.\,]", text)
        if m:
            terms.scope.research_area = m.group(1).strip()

        # Bulleted deliverables
        bullets = re.findall(r"(?:^|\n)\s*[-•]\s*([^\n]{10,200})", text)
        terms.scope.deliverables = [b.strip() for b in bullets[:6]]

    # ─── Tier 2: Claude enrichment ──────────────────────────────────────────

    def _enrich_with_claude(self, text: str, terms: ContractTerms) -> None:
        try:
            import anthropic
        except ImportError:
            terms.needs_review.append("anthropic SDK not installed")
            return

        client = anthropic.Anthropic(api_key=self.api_key)
        prompt = self._claude_prompt(text)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            raw = msg.content[0].text
        except Exception:
            return
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            return
        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return

        # Merge into terms
        if data.get("objectives"):
            terms.scope.objectives = data["objectives"]
        if data.get("deliverables") and not terms.scope.deliverables:
            terms.scope.deliverables = data["deliverables"]
        if data.get("special_clauses"):
            terms.special_clauses = data["special_clauses"]
        if data.get("obligations"):
            for o in data["obligations"]:
                terms.obligations.append(Obligation(
                    party=o.get("party", ""),
                    description=o.get("description", ""),
                    deadline=o.get("deadline", ""),
                ))
        if data.get("milestones"):
            for m in data["milestones"]:
                terms.timeline.milestones.append(Milestone(
                    description=m.get("description", ""),
                    target_date=m.get("target_date", ""),
                ))
        if not terms.confidentiality.scope and data.get("confidentiality_scope"):
            terms.confidentiality.scope = data["confidentiality_scope"]

    @staticmethod
    def _claude_prompt(text: str) -> str:
        # Truncate long contracts to fit a single request
        excerpt = text if len(text) < 12000 else text[:12000]
        return f"""You are a contract analyst. Extract ONLY the terms that are
explicitly stated in the following collaboration agreement. Do not infer or
hallucinate any term that is not in the text. Return strict JSON with these
keys (all optional):

{{
  "objectives": ["..."],
  "deliverables": ["..."],
  "special_clauses": ["any non-standard or unusual clauses"],
  "obligations": [{{"party": "...", "description": "...", "deadline": "..."}}],
  "milestones": [{{"description": "...", "target_date": "..."}}],
  "confidentiality_scope": "summary in 1 sentence"
}}

Agreement text:
<<<
{excerpt}
>>>
"""


__all__ = [
    "ContractParser",
    "ContractTerms",
    "ContractDiff",
    "FieldDiff",
    "Party",
    "Milestone",
    "CollaborationScope",
    "Timeline",
    "FinancialTerms",
    "IPTerms",
    "ConfidentialityTerms",
    "TerminationTerms",
    "Obligation",
    "TEMPLATES",
]
