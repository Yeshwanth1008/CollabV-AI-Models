"""
Seed sample company job postings for AI Matching Engine 9.

Job postings don't exist anywhere else on the platform (CollabV otherwise
matches companies with professor-owned patents, not job-seekers with
roles), so there's no real data source to pull from yet - this generates a
realistic, idempotent set of ~18 postings spanning several industries and a
mix of internship/full-time and remote/on-site, giving the Student
Dashboard's "AI Matching Engine 9" tab (and its filters) real data to demo
against. Company-side "post a job" UI is an explicit follow-up, not part of
this pass - see POST /jobs for the API a future UI would call.

Idempotent: deletes any previously-seeded rows (job_id LIKE 'JOB-SEED-%')
before inserting, so re-running this script is safe.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from collabv.job_matching_db import DEFAULT_DB_PATH, init_job_matching_tables, save_job_posting  # noqa: E402
from collabv.need_parser import DOMAIN_PATTERNS, TECH_STACK_PATTERNS, _match_patterns  # noqa: E402

JOBS = [
    {
        "company_name": "NeuralArc AI",
        "title": "Machine Learning Engineer Intern",
        "description": (
            "Work with our applied ML team building deep learning models for computer vision "
            "and NLP products. You'll fine-tune transformer models, build data pipelines, and "
            "ship features used by real customers. Great fit for someone who has trained neural "
            "networks with PyTorch or TensorFlow and enjoys fast iteration."
        ),
        "required_skills": ["Python", "PyTorch", "Machine Learning", "Deep Learning"],
        "preferred_skills": ["TensorFlow", "Computer Vision", "NLP", "Docker"],
        "min_experience_years": 0,
        "education_requirement": "Pursuing B.Tech/B.E in Computer Science or related field",
        "certifications_preferred": ["Deep Learning Specialization", "TensorFlow Developer Certificate"],
        "employment_type": "internship",
        "is_remote": True,
        "location": "Remote",
    },
    {
        "company_name": "Finlytics Systems",
        "title": "Backend Software Engineer",
        "description": (
            "Build and scale backend services powering our fintech platform - REST APIs, "
            "event-driven pipelines, and a PostgreSQL-backed ledger system. You'll work closely "
            "with product and design in an agile team, with strong emphasis on testing and "
            "reliability. Experience with Python or Java backend frameworks required."
        ),
        "required_skills": ["Python", "SQL", "REST APIs", "PostgreSQL"],
        "preferred_skills": ["Docker", "Kubernetes", "AWS", "Java"],
        "min_experience_years": 2,
        "education_requirement": "Bachelor's in Computer Science or related",
        "certifications_preferred": ["AWS Certified Developer"],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Bengaluru",
    },
    {
        "company_name": "TerraSense Robotics",
        "title": "Robotics Software Engineer",
        "description": (
            "Design and implement autonomous navigation and manipulation software for our "
            "agricultural robots. You'll work on SLAM, motion planning, and ROS-based control "
            "systems, collaborating with hardware and perception teams to get robots working "
            "reliably in unstructured outdoor environments."
        ),
        "required_skills": ["ROS", "C++", "Python", "Robotics"],
        "preferred_skills": ["SLAM", "Path Planning", "Computer Vision", "Embedded Systems"],
        "min_experience_years": 1,
        "education_requirement": "Bachelor's in Robotics, Mechanical, or Computer Engineering",
        "certifications_preferred": [],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Chennai",
    },
    {
        "company_name": "DataForge Analytics",
        "title": "Data Science Intern",
        "description": (
            "Support our analytics team building predictive models and dashboards for retail "
            "clients. You'll clean and explore large datasets, build statistical/ML models, and "
            "present findings to stakeholders. Comfort with Python's data stack and SQL expected."
        ),
        "required_skills": ["Python", "SQL", "Data Science", "Statistics"],
        "preferred_skills": ["Scikit-learn", "Pandas", "Data Visualization", "Machine Learning"],
        "min_experience_years": 0,
        "education_requirement": "Pursuing degree in Computer Science, Statistics, or related",
        "certifications_preferred": ["Google Data Analytics Certificate"],
        "employment_type": "internship",
        "is_remote": True,
        "location": "Remote",
    },
    {
        "company_name": "CipherGuard Security",
        "title": "Cybersecurity Analyst",
        "description": (
            "Join our security operations team monitoring, detecting, and responding to threats "
            "across client networks. You'll work with SIEM tooling, run vulnerability assessments, "
            "and help harden systems against intrusion. Knowledge of network security and "
            "cryptography fundamentals is important."
        ),
        "required_skills": ["Network Security", "Cybersecurity", "Cryptography"],
        "preferred_skills": ["Penetration Testing", "SIEM", "Python", "Cloud Security"],
        "min_experience_years": 1,
        "education_requirement": "Bachelor's in Computer Science, IT, or related",
        "certifications_preferred": ["CompTIA Security+", "Certified Ethical Hacker (CEH)"],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Hyderabad",
    },
    {
        "company_name": "CloudPeak Technologies",
        "title": "DevOps Engineer",
        "description": (
            "Own our CI/CD pipelines and cloud infrastructure across AWS and Kubernetes. You'll "
            "automate deployments, improve observability, and work with engineering teams to "
            "keep production systems reliable and cost-efficient."
        ),
        "required_skills": ["AWS", "Kubernetes", "Docker", "CI/CD"],
        "preferred_skills": ["Terraform", "Python", "Linux", "Monitoring"],
        "min_experience_years": 2,
        "education_requirement": "Bachelor's in Computer Science or related",
        "certifications_preferred": ["AWS Certified Solutions Architect", "Certified Kubernetes Administrator"],
        "employment_type": "full_time",
        "is_remote": True,
        "location": "Remote",
    },
    {
        "company_name": "VitalSense Biotech",
        "title": "Bioinformatics Research Intern",
        "description": (
            "Support our genomics research team analyzing sequencing data to identify disease "
            "biomarkers. You'll build data processing pipelines, apply statistical genomics "
            "methods, and contribute to publications. Background in biology plus programming "
            "skills strongly preferred."
        ),
        "required_skills": ["Bioinformatics", "Python", "Statistics"],
        "preferred_skills": ["R", "Genomics", "Machine Learning", "Data Science"],
        "min_experience_years": 0,
        "education_requirement": "Pursuing degree in Biotechnology, Bioinformatics, or related",
        "certifications_preferred": [],
        "employment_type": "internship",
        "is_remote": False,
        "location": "Pune",
    },
    {
        "company_name": "IronWorks Manufacturing",
        "title": "Mechanical Design Engineer",
        "description": (
            "Design and validate mechanical components for our industrial equipment line using "
            "CAD and FEA. You'll work through the full product cycle from concept to prototype "
            "to production, collaborating with manufacturing on DFM."
        ),
        "required_skills": ["SolidWorks", "AutoCAD", "Mechanical Design"],
        "preferred_skills": ["ANSYS", "Finite Element Analysis", "GD&T", "Manufacturing"],
        "min_experience_years": 2,
        "education_requirement": "Bachelor's in Mechanical Engineering",
        "certifications_preferred": ["Certified SolidWorks Professional"],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Coimbatore",
    },
    {
        "company_name": "BrightWave Renewable Energy",
        "title": "Renewable Energy Systems Engineer",
        "description": (
            "Work on design and performance modeling of solar and wind energy systems for "
            "utility-scale projects. You'll run simulations, analyze grid integration, and "
            "support field engineering teams during commissioning."
        ),
        "required_skills": ["Renewable Energy", "MATLAB", "Power Systems"],
        "preferred_skills": ["Solar PV Design", "Energy Modeling", "AutoCAD"],
        "min_experience_years": 1,
        "education_requirement": "Bachelor's in Electrical or Energy Engineering",
        "certifications_preferred": ["NABCEP PV Associate"],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Ahmedabad",
    },
    {
        "company_name": "PixelCraft Studio",
        "title": "UI/UX Design Intern",
        "description": (
            "Design intuitive, accessible interfaces for our consumer mobile apps. You'll run "
            "user research, build wireframes and high-fidelity prototypes in Figma, and work "
            "closely with engineers to ship polished experiences."
        ),
        "required_skills": ["Figma", "UI Design", "UX Research"],
        "preferred_skills": ["Prototyping", "Design Systems", "User Testing"],
        "min_experience_years": 0,
        "education_requirement": "Pursuing degree in Design, HCI, or related",
        "certifications_preferred": ["Google UX Design Certificate"],
        "employment_type": "internship",
        "is_remote": True,
        "location": "Remote",
    },
    {
        "company_name": "LedgerChain Labs",
        "title": "Blockchain Developer",
        "description": (
            "Build smart contracts and decentralized applications for our Web3 platform. You'll "
            "write and audit Solidity contracts, integrate with EVM-compatible chains, and help "
            "design token-economics-aware protocols."
        ),
        "required_skills": ["Solidity", "Blockchain", "Smart Contracts"],
        "preferred_skills": ["Ethereum", "Web3.js", "Cryptography", "JavaScript"],
        "min_experience_years": 1,
        "education_requirement": "Bachelor's in Computer Science or related",
        "certifications_preferred": ["Certified Blockchain Developer"],
        "employment_type": "full_time",
        "is_remote": True,
        "location": "Remote",
    },
    {
        "company_name": "SiliconEdge Semiconductors",
        "title": "VLSI Design Engineer",
        "description": (
            "Design and verify digital IP blocks for our next-generation SoCs. You'll work on "
            "RTL design in Verilog, run simulations, and collaborate with the physical design team "
            "on timing closure."
        ),
        "required_skills": ["Verilog", "VLSI", "Digital Design"],
        "preferred_skills": ["FPGA", "Synthesis", "Static Timing Analysis"],
        "min_experience_years": 1,
        "education_requirement": "Bachelor's/Master's in Electronics or VLSI Design",
        "certifications_preferred": [],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Bengaluru",
    },
    {
        "company_name": "GreenRoots AgriTech",
        "title": "IoT Systems Engineer Intern",
        "description": (
            "Build IoT sensor networks and embedded firmware for precision agriculture products "
            "measuring soil moisture, weather, and crop health. You'll work across embedded C, "
            "wireless protocols, and cloud data ingestion."
        ),
        "required_skills": ["Embedded Systems", "IoT", "C"],
        "preferred_skills": ["Microcontroller", "Sensor", "Python", "AWS"],
        "min_experience_years": 0,
        "education_requirement": "Pursuing degree in Electronics, ECE, or related",
        "certifications_preferred": [],
        "employment_type": "internship",
        "is_remote": False,
        "location": "Pune",
    },
    {
        "company_name": "MarketPulse Digital",
        "title": "Digital Marketing Associate",
        "description": (
            "Plan and execute digital campaigns across search, social, and email for our B2B "
            "clients. You'll analyze campaign performance, run A/B tests, and communicate results "
            "to stakeholders. Strong written communication and stakeholder management skills a plus."
        ),
        "required_skills": ["Digital Marketing", "SEO", "Analytics"],
        "preferred_skills": ["Google Ads", "Content Strategy", "Communication"],
        "min_experience_years": 1,
        "education_requirement": "Bachelor's degree in any discipline",
        "certifications_preferred": ["Google Analytics Certification", "HubSpot Content Marketing"],
        "employment_type": "full_time",
        "is_remote": True,
        "location": "Remote",
    },
    {
        "company_name": "Nimbus Cloud Software",
        "title": "Full Stack Developer",
        "description": (
            "Build customer-facing features across our React frontend and Node.js backend. "
            "You'll own features end to end, write tests, and participate in code review. Comfort "
            "with SQL and REST API design expected."
        ),
        "required_skills": ["JavaScript", "React", "Node.js", "SQL"],
        "preferred_skills": ["TypeScript", "AWS", "Docker", "REST APIs"],
        "min_experience_years": 1,
        "education_requirement": "Bachelor's in Computer Science or related",
        "certifications_preferred": [],
        "employment_type": "full_time",
        "is_remote": True,
        "location": "Remote",
    },
    {
        "company_name": "ClearPath Structural Consultants",
        "title": "Structural Engineering Intern",
        "description": (
            "Assist senior engineers with structural analysis and design of buildings and bridges "
            "using finite element methods. You'll run FEA simulations, prepare drawings, and learn "
            "seismic design principles on live projects."
        ),
        "required_skills": ["Structural Analysis", "AutoCAD", "Finite Element Analysis"],
        "preferred_skills": ["ETABS", "STAAD.Pro", "Seismic Design"],
        "min_experience_years": 0,
        "education_requirement": "Pursuing degree in Civil or Structural Engineering",
        "certifications_preferred": [],
        "employment_type": "internship",
        "is_remote": False,
        "location": "Mumbai",
    },
    {
        "company_name": "Finlytics Systems",
        "title": "Product Manager - Fintech Platform",
        "description": (
            "Own the roadmap for our lending product line. You'll gather requirements from "
            "customers and internal stakeholders, write specs, and work daily with engineering "
            "and design to ship features. Strong leadership and cross-team communication needed."
        ),
        "required_skills": ["Product Management", "Communication", "Leadership"],
        "preferred_skills": ["Agile", "SQL", "Data Analysis", "Roadmapping"],
        "min_experience_years": 3,
        "education_requirement": "Bachelor's degree; MBA a plus",
        "certifications_preferred": ["Certified Scrum Product Owner"],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Bengaluru",
    },
    {
        "company_name": "NeuralArc AI",
        "title": "Computer Vision Engineer",
        "description": (
            "Develop production computer vision models for defect detection in manufacturing "
            "lines - object detection, segmentation, and real-time inference on edge devices. "
            "You'll take models from research to deployment."
        ),
        "required_skills": ["Computer Vision", "Python", "OpenCV", "Deep Learning"],
        "preferred_skills": ["Object Detection", "PyTorch", "Edge Computing", "Docker"],
        "min_experience_years": 2,
        "education_requirement": "Bachelor's/Master's in Computer Science or related",
        "certifications_preferred": ["Deep Learning Specialization"],
        "employment_type": "full_time",
        "is_remote": False,
        "location": "Chennai",
    },
]


def _derive_domain_fields(description: str, required_skills, preferred_skills):
    domain_tags = sorted(set(_match_patterns(description, DOMAIN_PATTERNS)))
    keywords = sorted(set(
        _match_patterns(description, TECH_STACK_PATTERNS)
        + [s for s in (required_skills + preferred_skills)]
    ))
    return domain_tags, keywords


def main() -> None:
    init_job_matching_tables(DEFAULT_DB_PATH)

    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.execute("DELETE FROM job_postings WHERE job_id LIKE 'JOB-SEED-%'")
    conn.commit()
    conn.close()

    created = []
    for i, job in enumerate(JOBS):
        domain_tags, keywords = _derive_domain_fields(
            job["description"], job["required_skills"], job["preferred_skills"],
        )
        job_id = f"JOB-SEED-{i:03d}"
        save_job_posting({
            "job_id": job_id,
            "company_id": f"COMP-SEED-{i:03d}",
            "company_name": job["company_name"],
            "title": job["title"],
            "description": job["description"],
            "required_skills": job["required_skills"],
            "preferred_skills": job["preferred_skills"],
            "min_experience_years": job["min_experience_years"],
            "education_requirement": job["education_requirement"],
            "certifications_preferred": job["certifications_preferred"],
            "keywords": keywords,
            "domain_tags": domain_tags,
            "employment_type": job["employment_type"],
            "is_remote": job["is_remote"],
            "location": job["location"],
            "status": "active",
        }, DEFAULT_DB_PATH)
        created.append((job_id, job["title"], job["company_name"], job["employment_type"], job["is_remote"]))

    print(f"Seeded {len(created)} job postings:\n")
    print(f"{'job_id':<16} {'title':<38} {'company':<26} {'type':<11} remote")
    for job_id, title, company, etype, remote in created:
        print(f"{job_id:<16} {title:<38} {company:<26} {etype:<11} {remote}")

    internships = sum(1 for j in JOBS if j["employment_type"] == "internship")
    remote = sum(1 for j in JOBS if j["is_remote"])
    print(f"\n{len(JOBS)} total - {internships} internship(s), {len(JOBS) - internships} full-time, {remote} remote")


if __name__ == "__main__":
    main()
