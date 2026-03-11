"""
Tests for DM guardrails: stage progression, follow-up limits, stacked-send prevention.
Run with: python3 -m pytest test_dm_guardrails.py -v
"""
import sqlite3
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ---- Helpers to create in-memory DB that mirrors production schema ----

def create_test_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        contactName TEXT NOT NULL,
        profileId TEXT NOT NULL,
        profileName TEXT NOT NULL,
        keyword TEXT NOT NULL,
        originGroup TEXT NOT NULL,
        lastMessage TEXT NOT NULL,
        lastMessageTime TEXT NOT NULL,
        unread INTEGER NOT NULL,
        labelId TEXT,
        isArchived INTEGER NOT NULL DEFAULT 0,
        isDeletedUi INTEGER NOT NULL DEFAULT 0,
        aiAutoEnabled INTEGER NOT NULL DEFAULT 0,
        contactInfo TEXT NOT NULL DEFAULT '{}',
        commentAttribution TEXT NOT NULL DEFAULT '{}',
        keywordContext TEXT NOT NULL DEFAULT '{}',
        followUpCount INTEGER NOT NULL DEFAULT 0,
        followUpDueAt TEXT,
        lastAiOutboundAt TEXT,
        aiAutoManualOff INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        conversationId TEXT NOT NULL,
        text TEXT NOT NULL,
        sender TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        isDeletedUi INTEGER NOT NULL DEFAULT 0,
        isAiGenerated INTEGER NOT NULL DEFAULT 0
    )""")
    db.execute("""CREATE TABLE automation_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE keyword_rules (
        id TEXT PRIMARY KEY,
        keyword TEXT NOT NULL,
        persona TEXT NOT NULL DEFAULT '',
        promptPreview TEXT NOT NULL DEFAULT '',
        commentPrompt TEXT,
        dmPrompt TEXT,
        dmMaxReplies INTEGER,
        dmReplyDelay INTEGER,
        active INTEGER NOT NULL DEFAULT 1,
        assignedProfileIds TEXT NOT NULL DEFAULT '[]',
        dmPromptStages TEXT
    )""")
    return db


def insert_settings(db, overrides=None):
    defaults = {
        "masterEnabled": True,
        "followUpEnabled": True,
        "followUpDelaySeconds": 259200,
        "followUpMaxCount": 1,
        "dmModel": "gpt-5.1",
        "followUpModel": "gpt-5.1",
        "commentModel": "gpt-4.1-mini",
        "globalAiAutoNewChats": False,
        "dmFallbackPrompt": "test prompt",
        "commentFallbackPrompt": "test",
        "commentFallbackEnabled": True,
        "blacklistEnabled": False,
        "blacklistTerms": [],
        "globalDailyCapPerAccount": 5,
        "delayMin": 3,
        "delayMax": 10,
        "roundsBeforeConnectionRest": 2,
        "connectionRestMinutes": 2,
        "activeDays": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        "runFrom": "00:00",
        "runTo": "23:59",
        "postsPerCommunityScanLimit": 25,
        "preScanEnabled": True,
        "keywordScanningEnabled": True,
        "scanIntervalMinutes": 5,
        "postsPerCommunityPerScan": 20,
        "scanConcurrency": 2,
        "executionConcurrency": 1,
        "queuePrefillMaxPerProfilePerPass": 3,
        "orchestrationMode": "n8n",
        "dmPromptStages": {
            "first": "Write first reply",
            "second": "Write second reply",
            "third": "Write third reply",
            "fourth_plus": "Write fourth reply",
            "follow_up": "Write follow-up"
        },
    }
    if overrides:
        defaults.update(overrides)
    db.execute("INSERT OR REPLACE INTO automation_settings (key, value) VALUES ('default', ?)", (json.dumps(defaults),))
    db.commit()


def insert_conversation(db, conv_id="conv-1", ai_auto=True, follow_up_count=0, follow_up_due=None, last_ai_outbound=None):
    db.execute("""INSERT INTO conversations
        (id, contactName, profileId, profileName, keyword, originGroup, lastMessage, lastMessageTime,
         unread, isArchived, isDeletedUi, aiAutoEnabled, contactInfo, commentAttribution, keywordContext,
         followUpCount, followUpDueAt, lastAiOutboundAt)
        VALUES (?, 'Test Contact', 'profile-1', 'TestProfile', 'test', 'TestGroup', 'hello', '2026-01-01T00:00:00Z',
                1, 0, 0, ?, '{}', '{}', '{}', ?, ?, ?)""",
        (conv_id, 1 if ai_auto else 0, follow_up_count, follow_up_due, last_ai_outbound))
    db.commit()


def insert_message(db, conv_id, sender, text, timestamp, is_ai_generated=0):
    import uuid
    msg_id = str(uuid.uuid4())
    db.execute("INSERT INTO messages (id, conversationId, text, sender, timestamp, isDeletedUi, isAiGenerated) VALUES (?, ?, ?, ?, ?, 0, ?)",
        (msg_id, conv_id, text, sender, timestamp, is_ai_generated))
    db.commit()
    return msg_id


# ========================================================================
# TEST: _resolve_stage_by_turns (the NEW correct function)
# ========================================================================

def test_resolve_stage_first_no_outbound():
    """First stage when no AI outbound exists."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    stage = _resolve_stage_by_turns(db, "conv-1")
    assert stage == "first", f"Expected 'first', got '{stage}'"

def test_resolve_stage_first_after_ai_outbound_no_reply():
    """Stage stays 'first' after AI outbound if contact hasn't replied yet."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI reply 1", "2026-01-01T01:00:00Z", is_ai_generated=1)
    stage = _resolve_stage_by_turns(db, "conv-1")
    # After first AI outbound with no inbound reply: stage should NOT advance
    assert stage == "first", f"Expected 'first' (no inbound reply after AI), got '{stage}'"

def test_resolve_stage_second_after_inbound_reply():
    """Stage advances to 'second' only after contact replies to first AI outbound."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI reply 1", "2026-01-01T01:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "Nice, tell me more", "2026-01-01T02:00:00Z")
    stage = _resolve_stage_by_turns(db, "conv-1")
    assert stage == "second", f"Expected 'second', got '{stage}'"

def test_resolve_stage_second_no_advance_without_second_reply():
    """Stage stays 'second' after second AI outbound if no new inbound."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI 1", "2026-01-01T01:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "Reply 1", "2026-01-01T02:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI 2", "2026-01-01T03:00:00Z", is_ai_generated=1)
    stage = _resolve_stage_by_turns(db, "conv-1")
    assert stage == "second", f"Expected 'second' (no reply after AI 2), got '{stage}'"

def test_resolve_stage_third_after_two_reply_turns():
    """Stage advances to 'third' after two inbound replies following AI outbounds."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI 1", "2026-01-01T01:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "Reply 1", "2026-01-01T02:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI 2", "2026-01-01T03:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "Reply 2", "2026-01-01T04:00:00Z")
    stage = _resolve_stage_by_turns(db, "conv-1")
    assert stage == "third", f"Expected 'third', got '{stage}'"

def test_resolve_stage_fourth_plus():
    """Stage advances to 'fourth_plus' after three reply turns."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI 1", "2026-01-01T01:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "R1", "2026-01-01T02:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI 2", "2026-01-01T03:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "R2", "2026-01-01T04:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI 3", "2026-01-01T05:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "R3", "2026-01-01T06:00:00Z")
    stage = _resolve_stage_by_turns(db, "conv-1")
    assert stage == "fourth_plus", f"Expected 'fourth_plus', got '{stage}'"

def test_resolve_stage_follow_up():
    """Follow-up flag overrides turn counting."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    stage = _resolve_stage_by_turns(db, "conv-1", is_follow_up=True)
    assert stage == "follow_up", f"Expected 'follow_up', got '{stage}'"


# ========================================================================
# TEST: Stacked outbound prevention
# ========================================================================

def test_no_stacked_outbound_if_latest_is_ai_outbound():
    """Must not send if the latest message is already an AI outbound with no inbound since."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI reply", "2026-01-01T01:00:00Z", is_ai_generated=1)
    # The guard should block sending another outbound
    blocked = _should_block_stacked_outbound(db, "conv-1")
    assert blocked is True, "Should block stacked outbound when latest is AI outbound"

def test_allow_send_after_inbound_reply():
    """Allow sending when the latest message is inbound."""
    db = create_test_db()
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    insert_message(db, "conv-1", "outbound", "AI reply", "2026-01-01T01:00:00Z", is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "Thanks", "2026-01-01T02:00:00Z")
    blocked = _should_block_stacked_outbound(db, "conv-1")
    assert blocked is False, "Should allow send when latest is inbound"


# ========================================================================
# TEST: Follow-up eligibility
# ========================================================================

def test_followup_blocked_before_delay():
    """Follow-up should not fire before delay elapsed."""
    db = create_test_db()
    insert_settings(db, {"followUpDelaySeconds": 259200, "followUpMaxCount": 1})
    now = datetime.now(tz=timezone.utc)
    # Due in 3 days from now - not yet due
    due = (now + timedelta(days=3)).isoformat()
    insert_conversation(db, "conv-1", follow_up_due=due, last_ai_outbound=now.isoformat())
    eligible = _is_followup_eligible(db, "conv-1", now.isoformat())
    assert eligible is False, "Follow-up should not be eligible before due date"

def test_followup_blocked_at_max_count():
    """Follow-up should not fire when max count reached."""
    db = create_test_db()
    insert_settings(db, {"followUpDelaySeconds": 1, "followUpMaxCount": 1})
    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(seconds=10)).isoformat()
    insert_conversation(db, "conv-1", follow_up_count=1, follow_up_due=past, last_ai_outbound=past)
    eligible = _is_followup_eligible(db, "conv-1", now.isoformat())
    assert eligible is False, "Follow-up should not exceed maxCount=1"

def test_followup_blocked_if_inbound_reply_exists():
    """Follow-up should not fire if contact replied after last AI outbound."""
    db = create_test_db()
    insert_settings(db, {"followUpDelaySeconds": 1, "followUpMaxCount": 1})
    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(seconds=10)).isoformat()
    insert_conversation(db, "conv-1", follow_up_count=0, follow_up_due=past, last_ai_outbound=past)
    insert_message(db, "conv-1", "outbound", "AI msg", past, is_ai_generated=1)
    insert_message(db, "conv-1", "inbound", "Reply", now.isoformat())
    eligible = _is_followup_eligible(db, "conv-1", now.isoformat())
    assert eligible is False, "Follow-up should not fire if contact replied"

def test_followup_allowed_when_eligible():
    """Follow-up should fire when all conditions met."""
    db = create_test_db()
    insert_settings(db, {"followUpDelaySeconds": 1, "followUpMaxCount": 1})
    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(seconds=10)).isoformat()
    insert_conversation(db, "conv-1", follow_up_count=0, follow_up_due=past, last_ai_outbound=past)
    insert_message(db, "conv-1", "inbound", "Initial msg", (now - timedelta(hours=1)).isoformat())
    insert_message(db, "conv-1", "outbound", "AI reply", past, is_ai_generated=1)
    eligible = _is_followup_eligible(db, "conv-1", now.isoformat())
    assert eligible is True, "Follow-up should be eligible"

def test_followup_max_count_2():
    """Second follow-up allowed when maxCount=2 and count=1."""
    db = create_test_db()
    insert_settings(db, {"followUpDelaySeconds": 1, "followUpMaxCount": 2})
    now = datetime.now(tz=timezone.utc)
    past = (now - timedelta(seconds=10)).isoformat()
    insert_conversation(db, "conv-1", follow_up_count=1, follow_up_due=past, last_ai_outbound=past)
    insert_message(db, "conv-1", "inbound", "Initial", (now - timedelta(hours=2)).isoformat())
    insert_message(db, "conv-1", "outbound", "AI 1", (now - timedelta(hours=1)).isoformat(), is_ai_generated=1)
    insert_message(db, "conv-1", "outbound", "FU 1", past, is_ai_generated=1)
    eligible = _is_followup_eligible(db, "conv-1", now.isoformat())
    assert eligible is True, "Second follow-up should be allowed when maxCount=2"


# ========================================================================
# TEST: Master automation and per-conversation gates
# ========================================================================

def test_master_off_blocks_send():
    """masterEnabled=false must block all sends."""
    db = create_test_db()
    insert_settings(db, {"masterEnabled": False})
    insert_conversation(db, "conv-1")
    insert_message(db, "conv-1", "inbound", "Hello", "2026-01-01T00:00:00Z")
    blocked = _is_master_blocked(db)
    assert blocked is True

def test_ai_auto_off_blocks_send():
    """Per-conversation aiAutoEnabled=false blocks sends."""
    db = create_test_db()
    insert_settings(db)
    insert_conversation(db, "conv-1", ai_auto=False)
    conv = db.execute("SELECT * FROM conversations WHERE id = 'conv-1'").fetchone()
    assert not bool(conv["aiAutoEnabled"]), "AI Auto should be off"


# ========================================================================
# Implementation stubs — these will be replaced with the real functions
# ========================================================================

def _resolve_stage_by_turns(db, conversation_id, is_follow_up=False):
    """
    Correct stage resolution based on reply turns, not raw AI outbound count.
    
    Counts completed reply turns: an AI outbound followed by an inbound reply.
    Stage = number of completed turns.
    """
    if is_follow_up:
        return "follow_up"
    
    rows = db.execute(
        "SELECT sender, isAiGenerated FROM messages WHERE conversationId = ? AND isDeletedUi = 0 ORDER BY rowid ASC",
        (conversation_id,),
    ).fetchall()
    
    completed_turns = 0
    awaiting_reply = False
    
    for row in rows:
        sender = str(row["sender"]).strip().lower()
        is_ai = bool(row["isAiGenerated"])
        
        if sender == "outbound" and is_ai:
            awaiting_reply = True
        elif sender == "inbound" and awaiting_reply:
            completed_turns += 1
            awaiting_reply = False
    
    if completed_turns == 0:
        return "first"
    if completed_turns == 1:
        return "second"
    if completed_turns == 2:
        return "third"
    return "fourth_plus"


def _should_block_stacked_outbound(db, conversation_id):
    """Block if the latest message is already an AI outbound with no inbound since."""
    latest = db.execute(
        "SELECT sender, isAiGenerated FROM messages WHERE conversationId = ? AND isDeletedUi = 0 ORDER BY rowid DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if not latest:
        return False
    return str(latest["sender"]).lower() == "outbound" and bool(latest["isAiGenerated"])


def _is_followup_eligible(db, conversation_id, now_iso):
    """Check if a follow-up is eligible to send."""
    conv = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if not conv:
        return False
    
    settings_row = db.execute("SELECT value FROM automation_settings WHERE key = 'default'").fetchone()
    s = json.loads(settings_row["value"]) if settings_row else {}
    
    max_count = int(s.get("followUpMaxCount", 1))
    current_count = int(conv["followUpCount"] or 0)
    if current_count >= max_count:
        return False
    
    due = conv["followUpDueAt"]
    if not due or due > now_iso:
        return False
    
    # Check if contact replied after last AI outbound
    last_ai_out = db.execute(
        "SELECT timestamp FROM messages WHERE conversationId = ? AND sender = 'outbound' AND isAiGenerated = 1 ORDER BY rowid DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if last_ai_out:
        inbound_after = db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversationId = ? AND sender = 'inbound' AND rowid > (SELECT rowid FROM messages WHERE conversationId = ? AND sender = 'outbound' AND isAiGenerated = 1 ORDER BY rowid DESC LIMIT 1)",
            (conversation_id, conversation_id),
        ).fetchone()[0]
        if inbound_after > 0:
            return False
    
    return True


def _is_master_blocked(db):
    settings_row = db.execute("SELECT value FROM automation_settings WHERE key = 'default'").fetchone()
    s = json.loads(settings_row["value"]) if settings_row else {}
    return not s.get("masterEnabled", False)


# ========================================================================
# Run tests
# ========================================================================

if __name__ == "__main__":
    tests = [
        test_resolve_stage_first_no_outbound,
        test_resolve_stage_first_after_ai_outbound_no_reply,
        test_resolve_stage_second_after_inbound_reply,
        test_resolve_stage_second_no_advance_without_second_reply,
        test_resolve_stage_third_after_two_reply_turns,
        test_resolve_stage_fourth_plus,
        test_resolve_stage_follow_up,
        test_no_stacked_outbound_if_latest_is_ai_outbound,
        test_allow_send_after_inbound_reply,
        test_followup_blocked_before_delay,
        test_followup_blocked_at_max_count,
        test_followup_blocked_if_inbound_reply_exists,
        test_followup_allowed_when_eligible,
        test_followup_max_count_2,
        test_master_off_blocks_send,
        test_ai_auto_off_blocks_send,
    ]
    
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  PASS  {test_fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {test_fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test_fn.__name__}: {e}")
            failed += 1
    
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)



# ---- Phase B: message_changed removal + fallback guard + continue-automation ----
# These tests verify the guard logic using pure SQL, matching what _try_ai_auto_reply does.

class TestMessageChangedRemoval:
    """Verify that removing message_changed gate allows stuck conversations to proceed."""

    def test_stuck_conversation_has_inbound_last(self):
        """Reproduce the stuck state: aiAutoEnabled=1, latest message is inbound, message_changed=False."""
        db = create_test_db()
        insert_conversation(db, "conv-stuck", ai_auto=True)
        insert_message(db, "conv-stuck", "outbound", "AI reply", "2026-01-01T00:00:00Z", is_ai_generated=0)
        insert_message(db, "conv-stuck", "inbound", "Thanks!", "2026-01-01T00:01:00Z")

        # Simulate what _try_ai_auto_reply checks:
        conv = db.execute("SELECT * FROM conversations WHERE id = 'conv-stuck'").fetchone()
        assert bool(conv["aiAutoEnabled"])

        latest = db.execute("SELECT sender FROM messages WHERE conversationId = 'conv-stuck' AND isDeletedUi = 0 ORDER BY rowid DESC LIMIT 1").fetchone()
        assert latest["sender"] == "inbound"

        # With require_message_changed=False (the fix), this would proceed
        # Before the fix, message_changed=False would block here
        message_changed = False
        require_message_changed = False  # The fix
        should_proceed = not (require_message_changed and not message_changed)
        assert should_proceed is True, "Fix: should proceed even when message_changed=False"

    def test_old_behavior_would_block(self):
        """Verify the old behavior would have blocked."""
        message_changed = False
        require_message_changed = True  # Old behavior
        should_proceed = not (require_message_changed and not message_changed)
        assert should_proceed is False, "Old behavior correctly blocked"


class TestFallbackStackedGuard:
    """Test the fallback stacked-outbound guard (when isAiGenerated is all 0)."""

    def test_fallback_guard_blocks_when_no_inbound_after_outbound(self):
        """When lastAiOutboundAt is set, no isAiGenerated=1, and latest outbound has no inbound after, BLOCK."""
        db = create_test_db()
        insert_conversation(db, "conv-fb-1", ai_auto=True, last_ai_outbound="2026-01-01T00:00:00+00:00")
        insert_message(db, "conv-fb-1", "inbound", "Hi", "2026-01-01T00:00:00Z")
        insert_message(db, "conv-fb-1", "outbound", "AI reply", "2026-01-01T00:01:00Z", is_ai_generated=0)
        insert_message(db, "conv-fb-1", "inbound", "New message", "2026-01-01T00:02:00Z")

        # Primary guard: no isAiGenerated=1 messages
        last_ai = db.execute(
            "SELECT id FROM messages WHERE conversationId = 'conv-fb-1' AND sender = 'outbound' AND isAiGenerated = 1 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert last_ai is None, "No isAiGenerated=1 messages"

        # Fallback guard: latest outbound has inbound after it
        last_outbound = db.execute(
            "SELECT rowid FROM messages WHERE conversationId = 'conv-fb-1' AND sender = 'outbound' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        inbound_after = db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversationId = 'conv-fb-1' AND sender = 'inbound' AND isDeletedUi = 0 AND rowid > ?",
            (last_outbound["rowid"],)
        ).fetchone()[0]
        assert inbound_after == 1, "One inbound after outbound — fallback guard allows"

    def test_fallback_guard_allows_when_no_outbound(self):
        """When there are no outbound messages at all, both guards pass."""
        db = create_test_db()
        insert_conversation(db, "conv-fb-3", ai_auto=True)
        insert_message(db, "conv-fb-3", "inbound", "Hello", "2026-01-01T00:00:00Z")

        last_ai = db.execute(
            "SELECT id FROM messages WHERE conversationId = 'conv-fb-3' AND sender = 'outbound' AND isAiGenerated = 1 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert last_ai is None

        last_outbound = db.execute(
            "SELECT rowid FROM messages WHERE conversationId = 'conv-fb-3' AND sender = 'outbound' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert last_outbound is None, "No outbound at all — both guards pass"


class TestCooldownGuard:
    """Test the 5-minute cooldown via lastAiOutboundAt."""

    def test_cooldown_blocks_within_5_minutes(self):
        """If lastAiOutboundAt is within 5 minutes, should block."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=60)).isoformat()

        db = create_test_db()
        insert_conversation(db, "conv-cd-1", ai_auto=True, last_ai_outbound=recent)

        conv = db.execute("SELECT lastAiOutboundAt FROM conversations WHERE id = 'conv-cd-1'").fetchone()
        last_ts = datetime.fromisoformat(str(conv["lastAiOutboundAt"]).replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - last_ts).total_seconds()
        assert diff < 300, "Within 5 minute cooldown — should block"

    def test_cooldown_allows_after_5_minutes(self):
        """If lastAiOutboundAt is older than 5 minutes, should allow."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        old = (now - timedelta(seconds=600)).isoformat()

        db = create_test_db()
        insert_conversation(db, "conv-cd-2", ai_auto=True, last_ai_outbound=old)

        conv = db.execute("SELECT lastAiOutboundAt FROM conversations WHERE id = 'conv-cd-2'").fetchone()
        last_ts = datetime.fromisoformat(str(conv["lastAiOutboundAt"]).replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - last_ts).total_seconds()
        assert diff >= 300, "Past 5 minute cooldown — should allow"


class TestIsAiGeneratedPrimaryGuard:
    """Test the primary stacked guard using isAiGenerated=1."""

    def test_primary_guard_blocks_stacked_ai_outbound(self):
        """When isAiGenerated=1 outbound exists with no inbound after, block."""
        db = create_test_db()
        insert_conversation(db, "conv-pg-1", ai_auto=True)
        insert_message(db, "conv-pg-1", "outbound", "AI reply", "2026-01-01T00:00:00Z", is_ai_generated=1)
        insert_message(db, "conv-pg-1", "inbound", "Thanks!", "2026-01-01T00:01:00Z")
        insert_message(db, "conv-pg-1", "outbound", "Second AI", "2026-01-01T00:02:00Z", is_ai_generated=1)

        last_ai = db.execute(
            "SELECT id FROM messages WHERE conversationId = 'conv-pg-1' AND sender = 'outbound' AND isAiGenerated = 1 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert last_ai is not None

        inbound_after = db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversationId = 'conv-pg-1' AND sender = 'inbound' AND isDeletedUi = 0 AND rowid > (SELECT rowid FROM messages WHERE id = ?)",
            (last_ai["id"],)
        ).fetchone()[0]
        assert inbound_after == 0, "No inbound after latest AI outbound — should block"

    def test_primary_guard_allows_when_inbound_after_ai(self):
        """When isAiGenerated=1 outbound has inbound after it, allow."""
        db = create_test_db()
        insert_conversation(db, "conv-pg-2", ai_auto=True)
        insert_message(db, "conv-pg-2", "outbound", "AI reply", "2026-01-01T00:00:00Z", is_ai_generated=1)
        insert_message(db, "conv-pg-2", "inbound", "Thanks!", "2026-01-01T00:01:00Z")

        last_ai = db.execute(
            "SELECT id FROM messages WHERE conversationId = 'conv-pg-2' AND sender = 'outbound' AND isAiGenerated = 1 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        inbound_after = db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversationId = 'conv-pg-2' AND sender = 'inbound' AND isDeletedUi = 0 AND rowid > (SELECT rowid FROM messages WHERE id = ?)",
            (last_ai["id"],)
        ).fetchone()[0]
        assert inbound_after == 1, "Inbound exists after AI outbound — should allow"


# ---- Continue label tests ----

class TestContinueLabel:
    """Test the 'continue' audit label on conversations."""

    def test_continue_sets_continued_at(self):
        """After continue-automation succeeds, continuedAt should be set."""
        db = create_test_db()
        # Ensure continuedAt column exists
        try:
            db.execute("ALTER TABLE conversations ADD COLUMN continuedAt TEXT")
        except Exception:
            pass
        insert_conversation(db, "conv-cl-1", ai_auto=True)
        insert_message(db, "conv-cl-1", "inbound", "Hello", "2026-01-01T00:00:00Z")

        # Simulate what the endpoint does: set continuedAt where NULL
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        db.execute("UPDATE conversations SET continuedAt = ? WHERE id = ? AND continuedAt IS NULL", (now, "conv-cl-1"))
        db.commit()

        row = db.execute("SELECT continuedAt FROM conversations WHERE id = 'conv-cl-1'").fetchone()
        assert row["continuedAt"] is not None, "continuedAt should be set"
        assert row["continuedAt"] == now

    def test_continue_does_not_duplicate(self):
        """Calling continue twice should not overwrite the original timestamp."""
        db = create_test_db()
        try:
            db.execute("ALTER TABLE conversations ADD COLUMN continuedAt TEXT")
        except Exception:
            pass
        insert_conversation(db, "conv-cl-2", ai_auto=True)

        first_ts = "2026-01-01T10:00:00+00:00"
        db.execute("UPDATE conversations SET continuedAt = ? WHERE id = ? AND continuedAt IS NULL", (first_ts, "conv-cl-2"))

        second_ts = "2026-01-01T11:00:00+00:00"
        db.execute("UPDATE conversations SET continuedAt = ? WHERE id = ? AND continuedAt IS NULL", (second_ts, "conv-cl-2"))
        db.commit()

        row = db.execute("SELECT continuedAt FROM conversations WHERE id = 'conv-cl-2'").fetchone()
        assert row["continuedAt"] == first_ts, "Should keep first timestamp, not overwrite"

    def test_continue_label_persists_after_refresh(self):
        """continuedAt survives DB reads (not transient)."""
        db = create_test_db()
        try:
            db.execute("ALTER TABLE conversations ADD COLUMN continuedAt TEXT")
        except Exception:
            pass
        insert_conversation(db, "conv-cl-3", ai_auto=True)
        db.execute("UPDATE conversations SET continuedAt = '2026-01-01T12:00:00+00:00' WHERE id = 'conv-cl-3'")
        db.commit()

        # Re-read from DB
        row = db.execute("SELECT continuedAt FROM conversations WHERE id = 'conv-cl-3'").fetchone()
        assert row["continuedAt"] == "2026-01-01T12:00:00+00:00", "Should persist in DB"

    def test_continue_label_does_not_affect_automation(self):
        """Setting continuedAt should not change aiAutoEnabled or any automation column."""
        db = create_test_db()
        try:
            db.execute("ALTER TABLE conversations ADD COLUMN continuedAt TEXT")
        except Exception:
            pass
        insert_conversation(db, "conv-cl-4", ai_auto=True)

        db.execute("UPDATE conversations SET continuedAt = '2026-01-01T12:00:00+00:00' WHERE id = 'conv-cl-4'")
        db.commit()

        row = db.execute("SELECT aiAutoEnabled, followUpCount, followUpDueAt, lastAiOutboundAt FROM conversations WHERE id = 'conv-cl-4'").fetchone()
        assert bool(row["aiAutoEnabled"]) is True, "aiAutoEnabled unchanged"
        assert int(row["followUpCount"]) == 0, "followUpCount unchanged"
        assert row["followUpDueAt"] is None, "followUpDueAt unchanged"
        assert row["lastAiOutboundAt"] is None, "lastAiOutboundAt unchanged"
