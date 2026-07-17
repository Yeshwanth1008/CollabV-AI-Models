"""
Syncs search_platform's index from CollabV's live data (see
collabv_connector.py) — no mock, generated, or hand-written data anywhere
in this path. Safe to re-run anytime; does a full replace so deletions on
the CollabV side (e.g. a test row finally getting cleaned up, or a company
account being removed) are reflected here too, not just additions.

Run manually today; see ARCHITECTURE.md for how to put this on a schedule
(cron / Windows Task Scheduler) for closer-to-real-time sync once CollabV
has enough write volume to justify it.
"""
from .collabv_connector import load_all_live_profiles
from .db import Base, SessionLocal, engine
from .ingest import bulk_ingest
from .models import UserProfile


def main():
    Base.metadata.create_all(bind=engine)
    by_role = load_all_live_profiles()
    all_profiles = [p for profiles in by_role.values() for p in profiles]

    print("Live CollabV data pulled:")
    for role, profiles in by_role.items():
        print(f"  {role}: {len(profiles)}")
    for role in ("researcher", "startup", "alumni", "mentor"):
        print(f"  {role}: 0 (no table in CollabV's schema yet)")

    with SessionLocal() as db:
        existing = db.query(UserProfile).count()
        if existing:
            print(f"Clearing {existing} existing indexed profiles before full resync")
            db.query(UserProfile).delete()
            db.commit()
        count = bulk_ingest(db, all_profiles)
        print(f"Indexed {count} real CollabV profiles")


if __name__ == "__main__":
    main()
