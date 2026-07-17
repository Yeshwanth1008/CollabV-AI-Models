"""
CollabV AI - Company Need Parser
=================================
Converts free-text company problem descriptions into structured
matching fields. Primary: Claude API. Fallback: rule-based extraction.
"""

import os
import re
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ParsedNeed:
    technical_domains: List[str] = field(default_factory=list)
    required_expertise_tags: List[str] = field(default_factory=list)
    technology_stack: List[str] = field(default_factory=list)
    industry_sector: str = ""
    rd_type: str = ""  # "product", "process", "fundamental", "applied"
    collaboration_type: str = ""  # "Joint Research", "Consulting", "Sponsored Project"
    timeline_months: int = 12
    budget_tier: str = "medium"  # "low", "medium", "high"
    ip_preference: str = "shared"  # "shared", "company-owned", "open"
    matching_query: str = ""  # cleaned query for TF-IDF

    def to_company_request_fields(self) -> Dict:
        return {
            "technical_area": self.technical_domains,
            "required_expertise": self.required_expertise_tags,
            "tech_stack": self.technology_stack,
            "industry": self.industry_sector,
            "collaboration_type": self.collaboration_type,
            "research_level": self.rd_type,
            "project_description": self.matching_query,
        }


# ─── Claude API Parser ──────────────────────────────────────────────────────

CLAUDE_PROMPT = """You are an expert at analyzing company R&D needs for academic collaboration.

Given this company problem description, extract structured fields as JSON:

<description>
{text}
</description>

Return ONLY valid JSON with these fields:
{{
  "technical_domains": ["list of 2-5 technical domains, e.g. Machine Learning, Signal Processing, Materials Science"],
  "required_expertise_tags": ["list of 3-8 specific expertise tags, e.g. deep learning, NLP, finite element analysis"],
  "technology_stack": ["list of relevant tools/languages/frameworks, e.g. Python, TensorFlow, MATLAB, ANSYS"],
  "industry_sector": "single industry sector, e.g. Automotive, Pharma, IT/Software, Energy",
  "rd_type": "one of: applied, fundamental, product, process",
  "collaboration_type": "one of: Joint Research, Consulting, Sponsored Project, Technology Transfer",
  "timeline_months": integer estimate,
  "budget_tier": "one of: low, medium, high",
  "ip_preference": "one of: shared, company-owned, open",
  "matching_query": "a 2-3 sentence summary optimized for matching against professor profiles"
}}"""


def parse_with_claude(text: str) -> Optional[ParsedNeed]:
    """Use Claude API to parse company need text."""
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": CLAUDE_PROMPT.format(text=text),
            }],
        )

        content = response.content[0].text
        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return None
        data = json.loads(json_match.group())

        return ParsedNeed(
            technical_domains=data.get("technical_domains", []),
            required_expertise_tags=data.get("required_expertise_tags", []),
            technology_stack=data.get("technology_stack", []),
            industry_sector=data.get("industry_sector", ""),
            rd_type=data.get("rd_type", "applied"),
            collaboration_type=data.get("collaboration_type", "Joint Research"),
            timeline_months=data.get("timeline_months", 12),
            budget_tier=data.get("budget_tier", "medium"),
            ip_preference=data.get("ip_preference", "shared"),
            matching_query=data.get("matching_query", text[:500]),
        )
    except Exception:
        return None


# ─── Rule-Based Fallback Parser ─────────────────────────────────────────────

DOMAIN_PATTERNS = {
    # ML / AI
    "Machine Learning": [
        r"machine\s*learning", r"\bml\b", r"deep\s*learning", r"\bdl\b",
        r"neural\s*network", r"artificial\s*intelligence", r"\bai\b",
        r"reinforcement\s*learning", r"supervised\s*learning",
        r"unsupervised\s*learning", r"generative\s*model",
        r"large\s*language\s*model", r"\bllm\b", r"transformer",
        r"classification", r"regression\s*model", r"prediction\s*model",
    ],
    "Computer Vision": [
        r"computer\s*vision", r"image\s*processing", r"object\s*detection",
        r"image\s*recognition", r"video\s*analysis", r"lidar",
        r"point\s*cloud", r"segmentation", r"optical\s*character",
        r"\bocr\b", r"face\s*recognition", r"depth\s*estimation",
    ],
    "Natural Language Processing": [
        r"\bnlp\b", r"natural\s*language", r"text\s*mining",
        r"sentiment\s*analysis", r"speech\s*recognition",
        r"language\s*model", r"chatbot", r"conversational\s*ai",
        r"text\s*classification", r"information\s*extraction",
    ],
    "Signal Processing": [
        r"signal\s*processing", r"dsp\b", r"filter\s*design",
        r"fourier", r"spectral\s*analysis", r"wavelet",
        r"radar", r"sonar", r"antenna", r"rf\s*design",
        r"beamforming", r"mimo", r"5g", r"wireless",
        r"communication\s*system", r"modulation",
    ],
    "Robotics": [
        r"robot", r"autonomous", r"drone", r"uav\b",
        r"manipulation", r"path\s*planning", r"slam\b",
        r"kinematics", r"dynamics.*control", r"actuator",
        r"mechatronics", r"ros\b", r"motion\s*planning",
    ],
    "Materials Science": [
        r"material", r"alloy", r"composite", r"polymer",
        r"ceramic", r"nano\s*material", r"coating", r"corrosion",
        r"metallurg", r"fatigue", r"fracture\s*mechanics",
        r"additive\s*manufacturing", r"3d\s*print",
        r"thin\s*film", r"crystal", r"microstructure",
    ],
    "Structural Engineering": [
        r"structural", r"finite\s*element", r"fem\b", r"fea\b",
        r"stress\s*analysis", r"vibration", r"seismic",
        r"earthquake", r"bridge", r"building\s*design",
        r"concrete", r"reinforced", r"prestressed",
    ],
    "Fluid Mechanics": [
        r"fluid", r"cfd\b", r"computational\s*fluid",
        r"aerodynamic", r"turbulence", r"heat\s*transfer",
        r"thermodynamic", r"combustion", r"navier.stokes",
        r"flow\s*simulation", r"boundary\s*layer",
    ],
    "Biotechnology": [
        r"biotech", r"genomic", r"proteomic", r"bioinformatic",
        r"drug\s*discovery", r"molecular\s*biology", r"enzyme",
        r"fermentation", r"bioreactor", r"gene\s*editing",
        r"crispr", r"protein\s*engineering", r"biosensor",
        r"bioprocess", r"pharmaceutical", r"vaccine",
    ],
    "Chemical Engineering": [
        r"chemical\s*engineering", r"catalysis", r"catalyst",
        r"reaction\s*engineering", r"separation", r"distillation",
        r"membrane", r"process\s*optimization", r"refinery",
        r"petrochemical", r"electrochemistry", r"battery",
        r"fuel\s*cell", r"hydrogen", r"green\s*energy",
    ],
    "Environmental Engineering": [
        r"environment", r"pollution", r"wastewater",
        r"water\s*treatment", r"air\s*quality", r"emission",
        r"sustainability", r"climate", r"carbon\s*capture",
        r"renewable\s*energy", r"solar", r"wind\s*energy",
        r"water\s*quality", r"effluent", r"sewage", r"desalination",
        r"heavy\s*metal.*water", r"contaminant", r"remediation",
    ],
    "Ocean & Marine Engineering": [
        r"ocean", r"marine", r"offshore", r"coastal",
        r"subsea", r"underwater", r"ship", r"naval",
        r"wave\s*energy", r"tidal", r"port\b", r"maritime",
    ],
    "Electronics": [
        r"vlsi", r"semiconductor", r"integrated\s*circuit",
        r"embedded\s*system", r"fpga", r"asic",
        r"power\s*electronics", r"sensor", r"iot\b",
        r"internet\s*of\s*things", r"microcontroller",
        r"pcb\s*design", r"analog", r"digital\s*circuit",
    ],
    "Data Science": [
        r"data\s*science", r"big\s*data", r"analytics",
        r"data\s*mining", r"statistical\s*model",
        r"bayesian", r"time\s*series", r"optimization",
        r"recommendation\s*system", r"anomaly\s*detection",
    ],
    "Cybersecurity": [
        r"cyber\s*security", r"cryptograph", r"encryption",
        r"network\s*security", r"malware", r"intrusion\s*detection",
        r"penetration\s*test", r"blockchain", r"secure\s*protocol",
    ],
    "Biomedical Engineering": [
        r"biomedical", r"medical\s*device", r"prosthe",
        r"implant", r"rehabilitation", r"biomechanic",
        r"tissue\s*engineering", r"medical\s*imaging",
        r"wearable\s*health", r"diagnostic",
    ],
    "Ocean Engineering": [
        r"ocean", r"marine", r"offshore", r"subsea",
        r"coastal", r"ship", r"naval\s*architecture",
        r"wave\s*energy", r"port", r"underwater",
    ],
}

TECH_STACK_PATTERNS = {
    "Python": [r"\bpython\b"],
    "TensorFlow": [r"\btensorflow\b", r"\btf\b"],
    "PyTorch": [r"\bpytorch\b"],
    "MATLAB": [r"\bmatlab\b", r"\bsimulink\b"],
    "R": [r"\br\s+language\b", r"\br\s+programming\b"],
    "C++": [r"\bc\+\+\b", r"\bcpp\b"],
    "Java": [r"\bjava\b"],
    "ANSYS": [r"\bansys\b"],
    "COMSOL": [r"\bcomsol\b"],
    "AutoCAD": [r"\bautocad\b"],
    "SolidWorks": [r"\bsolidworks\b"],
    "CATIA": [r"\bcatia\b"],
    "Abaqus": [r"\babaqus\b"],
    "LabVIEW": [r"\blabview\b"],
    "OpenCV": [r"\bopencv\b"],
    "Scikit-learn": [r"\bscikit.learn\b", r"\bsklearn\b"],
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b"],
    "Docker": [r"\bdocker\b"],
    "AWS": [r"\baws\b", r"\bamazon\s*web\b"],
    "ROS": [r"\bros\b", r"robot\s*operating\s*system"],
    "FPGA": [r"\bfpga\b"],
    "Verilog": [r"\bverilog\b", r"\bvhdl\b"],
    "SQL": [r"\bsql\b", r"\bdatabase\b"],
    "Spark": [r"\bspark\b", r"\bhadoop\b"],
    "CFD": [r"\bcfd\b", r"openfoam"],
}

INDUSTRY_PATTERNS = {
    "WaterTech": [
        r"water\s*treatment", r"wastewater", r"water\s*quality", r"effluent",
        r"aqua", r"aquatic", r"desalination", r"sewage", r"water\s*monitor",
        r"water\s*purif", r"drinking\s*water", r"municipal\s*water",
    ],
    "EnvironmentalTech": [
        r"environmental", r"pollution", r"air\s*quality", r"emission",
        r"remediation", r"waste\s*management", r"recycling", r"sustainability",
        r"carbon\s*capture", r"green\s*tech",
    ],
    "MarineTech": [
        r"ocean", r"marine", r"offshore", r"coastal", r"subsea",
        r"naval", r"maritime", r"underwater", r"ship", r"port\b",
    ],
    "Automotive": [r"automo", r"vehicle", r"car\b", r"ev\b", r"electric\s*vehicle", r"adas"],
    "Aerospace": [r"aerospace", r"aviation", r"aircraft", r"satellite", r"space"],
    "IT/Software": [r"\bit\b", r"software", r"saas", r"cloud", r"fintech", r"edtech"],
    "Pharma": [r"pharma", r"drug", r"therapeutic", r"clinical\s*trial"],
    "Energy": [r"energy", r"oil\s*and\s*gas", r"renewable", r"solar", r"power\s*grid"],
    "Manufacturing": [r"manufactur", r"factory", r"production\s*line", r"quality\s*control"],
    "Healthcare": [r"healthcare", r"hospital", r"medical", r"health\s*tech", r"diagnostic"],
    "Telecom": [r"telecom", r"5g\b", r"wireless", r"network\s*operator"],
    "Construction": [r"construct", r"infrastructure", r"real\s*estate", r"cement"],
    "Agriculture": [r"agricul", r"agri\s*tech", r"crop", r"soil", r"irrigation"],
    "Defence": [r"defen[sc]e", r"military", r"defense\s*tech", r"weapon"],
    "Chemical": [r"chemical", r"petrochemical", r"refin"],
    "Steel/Metals": [r"steel", r"metal", r"mining", r"smelting", r"foundry"],
    "Biotech": [r"biotech", r"life\s*science", r"genomic"],
    "Electronics": [r"electronic", r"semiconductor", r"chip", r"circuit"],
}

COLLAB_PATTERNS = {
    "Joint Research": [r"joint\s*research", r"collaborative\s*research", r"co.develop", r"research\s*partner"],
    "Consulting": [r"consult", r"advisory", r"expert\s*opinion", r"technical\s*review"],
    "Sponsored Project": [r"sponsor", r"fund", r"grant", r"project\s*based"],
    "Technology Transfer": [r"technology\s*transfer", r"licens", r"patent", r"ip\s*transfer"],
}


def _match_patterns(text: str, pattern_dict: dict) -> List[str]:
    """Match text against pattern dictionary and return matching keys."""
    text_lower = text.lower()
    matches = []
    for key, patterns in pattern_dict.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                matches.append(key)
                break
    return matches


def _extract_expertise(text: str) -> List[str]:
    """Extract specific expertise tags from text."""
    tags = set()
    text_lower = text.lower()

    # Extract from domain matches
    for domain in _match_patterns(text, DOMAIN_PATTERNS):
        tags.add(domain.lower().replace(" ", "-"))

    # Extract specific technique mentions
    technique_patterns = [
        (r"deep\s*learning", "deep-learning"),
        (r"reinforcement\s*learning", "reinforcement-learning"),
        (r"transfer\s*learning", "transfer-learning"),
        (r"computer\s*vision", "computer-vision"),
        (r"natural\s*language", "NLP"),
        (r"finite\s*element", "FEA"),
        (r"computational\s*fluid", "CFD"),
        (r"molecular\s*dynamics", "molecular-dynamics"),
        (r"monte\s*carlo", "monte-carlo"),
        (r"optimization", "optimization"),
        (r"control\s*system", "control-systems"),
        (r"embedded\s*system", "embedded-systems"),
        (r"power\s*system", "power-systems"),
        (r"wireless\s*communication", "wireless-communications"),
        (r"drug\s*discovery", "drug-discovery"),
        (r"protein\s*engineering", "protein-engineering"),
        (r"gene\s*editing", "gene-editing"),
        (r"additive\s*manufacturing", "additive-manufacturing"),
        (r"3d\s*print", "3D-printing"),
        (r"nano\s*technolog", "nanotechnology"),
        (r"quantum\s*comput", "quantum-computing"),
        (r"edge\s*comput", "edge-computing"),
        (r"cloud\s*comput", "cloud-computing"),
    ]
    for pat, tag in technique_patterns:
        if re.search(pat, text_lower):
            tags.add(tag)

    return list(tags)[:8]


def _infer_rd_type(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ["product", "prototype", "mvp", "deploy", "production"]):
        return "product"
    if any(w in text_lower for w in ["process", "manufacturing", "scale up", "efficiency"]):
        return "process"
    if any(w in text_lower for w in ["fundamental", "theoretical", "basic research", "novel"]):
        return "fundamental"
    return "applied"


def _infer_timeline(text: str) -> int:
    m = re.search(r"(\d+)\s*months?", text.lower())
    if m:
        return min(int(m.group(1)), 36)
    if any(w in text.lower() for w in ["urgent", "quick", "fast", "immediate"]):
        return 6
    if any(w in text.lower() for w in ["long.term", "multi.year", "phd"]):
        return 24
    return 12


def _infer_budget(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ["large budget", "significant invest", "crore", "million"]):
        return "high"
    if any(w in text_lower for w in ["limited budget", "small scale", "pilot", "proof of concept"]):
        return "low"
    return "medium"


def parse_rule_based(text: str) -> ParsedNeed:
    """Rule-based fallback parser using 200+ keyword patterns."""
    domains = _match_patterns(text, DOMAIN_PATTERNS)
    tech_stack = _match_patterns(text, TECH_STACK_PATTERNS)
    industries = _match_patterns(text, INDUSTRY_PATTERNS)
    collab_types = _match_patterns(text, COLLAB_PATTERNS)
    expertise = _extract_expertise(text)

    return ParsedNeed(
        technical_domains=domains[:5],
        required_expertise_tags=expertise,
        technology_stack=tech_stack,
        industry_sector=industries[0] if industries else "General",
        rd_type=_infer_rd_type(text),
        collaboration_type=collab_types[0] if collab_types else "Joint Research",
        timeline_months=_infer_timeline(text),
        budget_tier=_infer_budget(text),
        ip_preference="shared",
        matching_query=text[:500],
    )


# ─── Public API ──────────────────────────────────────────────────────────────

def parse_need(text: str, use_claude: bool = True) -> ParsedNeed:
    """
    Parse company need text into structured fields.
    Tries Claude API first, falls back to rule-based.
    """
    if use_claude:
        result = parse_with_claude(text)
        if result:
            return result

    return parse_rule_based(text)
