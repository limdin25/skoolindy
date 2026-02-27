#!/usr/bin/env python3
"""Activity Timeline UTC fix: store timestamps with timezone to stop 'just now' skew."""
ENGINE = "/root/.openclaw/workspace/engageflow/backend/automation/engine.py"
APP = "/root/.openclaw/workspace/engageflow/backend/app.py"
DASHBOARD = "/root/.openclaw/workspace/engageflow/frontend/src/pages/DashboardPage.tsx"


def patch_engine():
    with open(ENGINE, "r") as f:
        s = f.read()
    # Replace activity_rows append timestamp
    s = s.replace(
        '"timestamp": datetime.now().isoformat(),',
        '"timestamp": datetime.now(timezone.utc).isoformat(),',
        1,
    )
    # Replace _persist_activity_rows fallbacks (two occurrences)
    s = s.replace(
        'row.get("timestamp", datetime.now().isoformat())',
        'row.get("timestamp", datetime.now(timezone.utc).isoformat())',
    )
    s = s.replace(
        'str(row.get("timestamp") or datetime.now().isoformat())',
        'str(row.get("timestamp") or datetime.now(timezone.utc).isoformat())',
    )
    with open(ENGINE, "w") as f:
        f.write(s)
    print("OK engine.py")


def patch_app():
    with open(APP, "r") as f:
        s = f.read()
    # Add timezone to imports
    s = s.replace(
        "from datetime import datetime, timedelta",
        "from datetime import datetime, timedelta, timezone",
    )
    # Replace now_display_time() at activity_feed INSERT sites (timestamp column)
    # Site 1: DM activity insert ~3989
    s = s.replace(
        '''                    (
                        str(uuid.uuid4()),
                        activity_profile,
                        origin_group,
                        activity_action,
                        now_display_time(),
                        activity_post_url,
                    ),''',
        '''                    (
                        str(uuid.uuid4()),
                        activity_profile,
                        origin_group,
                        activity_action,
                        datetime.now(timezone.utc).isoformat(),
                        activity_post_url,
                    ),''',
    )
    # Site 2: DM activity insert in message handler ~6733
    s = s.replace(
        '''                    (
                        str(uuid.uuid4()),
                        str(row["profileName"] or "SYSTEM"),
                        str(row["originGroup"] or "Skool Inbox"),
                        f"DM sent to {str(row['contactName'] or '').strip() or 'contact'}",
                        now_display_time(),
                        f"https://www.skool.com/chat?ch={chat_id}",
                    ),''',
        '''                    (
                        str(uuid.uuid4()),
                        str(row["profileName"] or "SYSTEM"),
                        str(row["originGroup"] or "Skool Inbox"),
                        f"DM sent to {str(row['contactName'] or '').strip() or 'contact'}",
                        datetime.now(timezone.utc).isoformat(),
                        f"https://www.skool.com/chat?ch={chat_id}",
                    ),''',
    )
    with open(APP, "w") as f:
        f.write(s)
    print("OK app.py")


def patch_dashboard():
    with open(DASHBOARD, "r") as f:
        s = f.read()
    # Legacy rows: naive ISO was stored in server TZ (Europe/Berlin).
    # Use SERVER_TIMEZONE instead of UK for naive strings.
    s = s.replace(
        'const UK_TIMEZONE = "Europe/London";',
        'const UK_TIMEZONE = "Europe/London";\nconst SERVER_TIMEZONE = "Europe/Berlin";',
    )
    s = s.replace(
        """  if (looksIsoNoZone) {
    const norm = text.replace(" ", "T");
    const m = norm.match(/^(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})/);
    if (!m) return Number.NaN;
    return zonedToEpoch(
      Number(m[1]),
      Number(m[2]),
      Number(m[3]),
      Number(m[4]),
      Number(m[5]),
      Number(m[6]),
      UK_TIMEZONE,
    );
  }""",
        """  if (looksIsoNoZone) {
    const norm = text.replace(" ", "T");
    const m = norm.match(/^(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})/);
    if (!m) return Number.NaN;
    return zonedToEpoch(
      Number(m[1]),
      Number(m[2]),
      Number(m[3]),
      Number(m[4]),
      Number(m[5]),
      Number(m[6]),
      SERVER_TIMEZONE,
    );
  }""",
    )
    with open(DASHBOARD, "w") as f:
        f.write(s)
    print("OK DashboardPage.tsx")


def main():
    patch_engine()
    patch_app()
    patch_dashboard()


if __name__ == "__main__":
    main()
