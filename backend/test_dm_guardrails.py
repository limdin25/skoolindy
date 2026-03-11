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
