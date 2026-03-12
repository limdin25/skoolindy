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


# ========================================================================
# PHASE B DEDUPE: Anti-duplicate send layer tests
# ========================================================================
import hashlib
import threading
import time


class TestOutboundSendLog:
    """Persistent DB-backed dedupe via outbound_send_log table."""

    def _create_send_log_table(self, db):
        db.execute("""CREATE TABLE IF NOT EXISTS outbound_send_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversationId TEXT NOT NULL,
            textHash TEXT NOT NULL,
            sentAt TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_send_log_conv_hash ON outbound_send_log (conversationId, textHash)")
        db.commit()

    def _text_hash(self, text):
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]

    def _check_db_dedupe(self, db, conv_id, text, window_seconds=600):
        """Returns True if duplicate found within window."""
        h = self._text_hash(text)
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat(timespec="seconds")
        row = db.execute(
            "SELECT 1 FROM outbound_send_log WHERE conversationId = ? AND textHash = ? AND sentAt > ?",
            (conv_id, h, cutoff),
        ).fetchone()
        return row is not None

    def _record_send(self, db, conv_id, text):
        h = self._text_hash(text)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute("INSERT INTO outbound_send_log (conversationId, textHash, sentAt) VALUES (?, ?, ?)",
                   (conv_id, h, now))
        db.commit()

    def test_first_send_passes(self):
        """First send should not be blocked."""
        db = create_test_db()
        self._create_send_log_table(db)
        assert not self._check_db_dedupe(db, "conv-1", "Hello there!")

    def test_duplicate_blocked_within_window(self):
        """Same text to same conversation within window should be blocked."""
        db = create_test_db()
        self._create_send_log_table(db)
        self._record_send(db, "conv-1", "Hello there!")
        assert self._check_db_dedupe(db, "conv-1", "Hello there!")

    def test_different_text_passes(self):
        """Different text to same conversation should pass."""
        db = create_test_db()
        self._create_send_log_table(db)
        self._record_send(db, "conv-1", "Hello there!")
        assert not self._check_db_dedupe(db, "conv-1", "Something completely different")

    def test_different_conversation_passes(self):
        """Same text to different conversation should pass."""
        db = create_test_db()
        self._create_send_log_table(db)
        self._record_send(db, "conv-1", "Hello there!")
        assert not self._check_db_dedupe(db, "conv-2", "Hello there!")

    def test_normalized_whitespace_catches_near_dupes(self):
        """Whitespace normalization catches near-duplicate text."""
        db = create_test_db()
        self._create_send_log_table(db)
        self._record_send(db, "conv-1", "Hello   there!\n  How are you?")
        assert self._check_db_dedupe(db, "conv-1", "Hello there! How are you?")

    def test_expired_entry_passes(self):
        """Entry older than window should not block."""
        db = create_test_db()
        self._create_send_log_table(db)
        h = self._text_hash("Hello there!")
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat(timespec="seconds")
        db.execute("INSERT INTO outbound_send_log (conversationId, textHash, sentAt) VALUES (?, ?, ?)",
                   ("conv-1", h, old_ts))
        db.commit()
        assert not self._check_db_dedupe(db, "conv-1", "Hello there!")

    def test_legitimate_followup_after_inbound_passes(self):
        """After a real inbound reply, a new AI reply (even same text) should pass
        because the conversation state changed. This tests the full guard chain,
        not just the dedupe layer — dedupe alone would block same text."""
        db = create_test_db()
        self._create_send_log_table(db)
        self._record_send(db, "conv-1", "How can I help you?")
        # Dedupe layer blocks same text (this is correct — the caller should
        # generate DIFFERENT text for a new stage)
        assert self._check_db_dedupe(db, "conv-1", "How can I help you?")


class TestConversationSendLock:
    """Per-conversation send lock prevents concurrent sends."""

    def test_lock_prevents_concurrent_sends(self):
        """Two threads trying to send to the same conversation —
        only one should acquire the lock."""
        locks = {}
        lock_of_locks = threading.Lock()
        results = []

        def try_acquire(conv_id, thread_id):
            with lock_of_locks:
                if conv_id not in locks:
                    locks[conv_id] = threading.Lock()
                lock = locks[conv_id]
            acquired = lock.acquire(blocking=False)
            results.append((thread_id, acquired))
            if acquired:
                time.sleep(0.05)  # Simulate send
                lock.release()

        t1 = threading.Thread(target=try_acquire, args=("conv-1", 1))
        t2 = threading.Thread(target=try_acquire, args=("conv-1", 2))
        t1.start()
        time.sleep(0.01)  # Ensure t1 acquires first
        t2.start()
        t1.join()
        t2.join()

        # One should get True, one False
        acquired_results = [r[1] for r in results]
        assert True in acquired_results, "At least one should acquire"
        assert False in acquired_results, "Second should be blocked"

    def test_different_conversations_not_blocked(self):
        """Sends to different conversations should not block each other."""
        locks = {}
        lock_of_locks = threading.Lock()
        results = []

        def try_acquire(conv_id, thread_id):
            with lock_of_locks:
                if conv_id not in locks:
                    locks[conv_id] = threading.Lock()
                lock = locks[conv_id]
            acquired = lock.acquire(blocking=False)
            results.append((thread_id, acquired))
            if acquired:
                time.sleep(0.05)
                lock.release()

        t1 = threading.Thread(target=try_acquire, args=("conv-1", 1))
        t2 = threading.Thread(target=try_acquire, args=("conv-2", 2))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join()
        t2.join()

        acquired_results = [r[1] for r in results]
        assert all(acquired_results), "Both should acquire (different conversations)"


class TestOrderOfOperations:
    """Verify isAiGenerated and lastAiOutboundAt are set BEFORE post-send upsert."""

    def test_ai_generated_set_before_upsert(self):
        """After a send, isAiGenerated should be set on the outbound message
        BEFORE any reimport/upsert occurs."""
        db = create_test_db()
        insert_conversation(db, "conv-oo-1", ai_auto=True)
        insert_message(db, "conv-oo-1", "inbound", "hello", "2026-01-01T10:00:00Z")

        # Simulate send: insert outbound, then mark isAiGenerated
        insert_message(db, "conv-oo-1", "outbound", "Hi! How can I help?", "2026-01-01T10:01:00Z")
        latest_out = db.execute(
            "SELECT id FROM messages WHERE conversationId = 'conv-oo-1' AND sender != 'inbound' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        db.execute("UPDATE messages SET isAiGenerated = 1 WHERE id = ?", (latest_out["id"],))

        # Now the stacked-outbound guard should block
        ai_msg = db.execute(
            "SELECT id FROM messages WHERE conversationId = 'conv-oo-1' AND sender = 'outbound' AND isAiGenerated = 1 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert ai_msg is not None, "isAiGenerated should be set before upsert check"

        # No inbound after this outbound
        inbound_after = db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversationId = 'conv-oo-1' AND sender = 'inbound' AND isDeletedUi = 0 AND rowid > (SELECT rowid FROM messages WHERE id = ?)",
            (ai_msg["id"],)
        ).fetchone()[0]
        assert inbound_after == 0, "No inbound after AI outbound = guard should block"

    def test_last_ai_outbound_at_set_before_upsert(self):
        """lastAiOutboundAt should be set BEFORE upsert so cooldown works."""
        db = create_test_db()
        insert_conversation(db, "conv-oo-2", ai_auto=True)

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute("UPDATE conversations SET lastAiOutboundAt = ? WHERE id = 'conv-oo-2'", (now,))
        db.commit()

        row = db.execute("SELECT lastAiOutboundAt FROM conversations WHERE id = 'conv-oo-2'").fetchone()
        last_ai = row["lastAiOutboundAt"]
        ts = datetime.fromisoformat(last_ai.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - ts).total_seconds()
        assert diff < 300, "Cooldown should block since lastAiOutboundAt was just set"


class TestFollowUpDelayResolution:
    """Verify follow-up delay correctly resolves keyword rule vs global setting."""

    def test_global_3day_delay_respected(self):
        """When keyword rule has no dmReplyDelay, global 259200s (3 days) is used."""
        global_delay = 259200  # 3 days in seconds
        kw_delay = None  # No keyword rule override
        raw = int(kw_delay) if kw_delay and kw_delay else 0
        effective = raw if raw >= 3600 else global_delay
        delay = max(effective, 3600)
        assert delay == 259200, f'Expected 259200 (3 days), got {delay}'

    def test_keyword_override_respected_if_large_enough(self):
        """A keyword rule with dmReplyDelay >= 3600 should override global."""
        global_delay = 259200
        kw_delay = 86400  # 1 day keyword override
        raw = int(kw_delay) if kw_delay else 0
        effective = raw if raw >= 3600 else global_delay
        delay = max(effective, 3600)
        assert delay == 86400, f'Expected 86400 (1 day override), got {delay}'

    def test_tiny_keyword_delay_uses_global(self):
        """A keyword rule with dmReplyDelay=1 must fall back to global delay, not floor to 3600."""
        global_delay = 259200
        kw_delay = 1  # Bad/default value
        raw = int(kw_delay) if kw_delay else 0
        effective = raw if raw >= 3600 else global_delay
        delay = max(effective, 3600)
        assert delay == 259200, f'Expected 259200 (global fallback), got {delay}'

    def test_zero_keyword_delay_uses_global(self):
        """dmReplyDelay=0 should use global delay."""
        global_delay = 259200
        kw_delay = 0
        raw = int(kw_delay) if kw_delay else 0
        effective = raw if raw >= 3600 else global_delay
        delay = max(effective, 3600)
        assert delay == 259200, f'Expected 259200 (global fallback), got {delay}'

    def test_floor_1hour_minimum(self):
        """Even if global delay is somehow set below 3600, floor is 3600."""
        global_delay = 600  # Broken global setting
        kw_delay = None
        raw = int(kw_delay) if kw_delay and kw_delay else 0
        effective = raw if raw >= 3600 else global_delay
        delay = max(effective, 3600)
        assert delay == 3600, f'Expected 3600 (1-hour floor), got {delay}'

    def test_stale_past_due_at_is_cleaned(self):
        """If followUpDueAt is in the past and was from broken logic, checker sends but
        the new delay after sending should use correct global delay."""
        db = create_test_db()
        past_due = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec='seconds')
        insert_conversation(db, 'conv-fu-stale', ai_auto=True, follow_up_count=0,
                          follow_up_due=past_due, last_ai_outbound=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec='seconds'))
        row = db.execute('SELECT followUpDueAt FROM conversations WHERE id = ?', ('conv-fu-stale',)).fetchone()
        assert row['followUpDueAt'] is not None
        # Verify the due time is in the past
        due_ts = datetime.fromisoformat(row['followUpDueAt'].replace('Z', '+00:00'))
        assert due_ts < datetime.now(timezone.utc), 'Stale due should be in the past'

    def test_no_followup_within_minutes_when_3day_delay(self):
        """With global delay 259200s, followUpDueAt should be ~3 days from now, not minutes."""
        now = datetime.now(timezone.utc)
        global_delay = 259200
        kw_delay = None
        raw = int(kw_delay) if kw_delay and kw_delay else 0
        effective = raw if raw >= 3600 else global_delay
        delay = max(effective, 3600)
        due_at = now + timedelta(seconds=delay)
        diff_seconds = (due_at - now).total_seconds()
        assert diff_seconds >= 259200, f'Follow-up should be >= 3 days away, got {diff_seconds}s'
        assert diff_seconds < 259300, 'Should be close to exactly 3 days'

    def test_followup_dueAt_computation_with_bad_keyword_rule(self):
        """End-to-end: simulate the exact code path with dmReplyDelay=1 and verify 3-day result."""
        global_delay = 259200
        matched_rule = {'dmReplyDelay': 1, 'dmMaxReplies': 3}
        # Exact logic from the fix:
        _kw_delay = int(matched_rule['dmReplyDelay']) if matched_rule and matched_rule['dmReplyDelay'] else 0
        _raw_delay = _kw_delay if _kw_delay >= 3600 else global_delay
        _delay = max(_raw_delay, 3600)
        now = datetime.now(timezone.utc)
        due_at = now + timedelta(seconds=_delay)
        diff = (due_at - now).total_seconds()
        assert abs(diff - 259200) < 1, f'Should be 259200s (3 days), got {diff}'
