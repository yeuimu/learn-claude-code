"""
Tests for v8_team_agent.py - Team collaboration & messaging.

13 unit tests for TeammateManager messaging, inbox, lifecycle, and task board sharing.
3 additional tests for broadcast, TEAMMATE_TOOLS, and shutdown flow.
4 LLM integration tests for multi-agent workflows.
"""
import os
import sys
import tempfile
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.helpers import get_client, run_agent, run_tests, MODEL
from tests.helpers import BASH_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL, EDIT_FILE_TOOL
from tests.helpers import TASK_CREATE_TOOL, TASK_LIST_TOOL, TASK_UPDATE_TOOL

from pathlib import Path
from v8_team_agent import TeammateManager, Teammate, TaskManager


# =============================================================================
# Unit Tests - TeammateManager
# =============================================================================

def test_create_team():
    tm = TeammateManager()
    result = tm.create_team("alpha-team")
    assert "created" in result.lower(), f"Expected 'created' in response, got: {result}"
    print("PASS: test_create_team")
    return True


def test_create_duplicate_team():
    tm = TeammateManager()
    tm.create_team("dup-team")
    result = tm.create_team("dup-team")
    assert "already exists" in result.lower(), f"Expected 'already exists', got: {result}"
    print("PASS: test_create_duplicate_team")
    return True


def test_send_message():
    tm = TeammateManager()
    tm.create_team("msg-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="alice", team_name="msg-team", inbox_path=inbox)
    tm._teams["msg-team"]["alice"] = teammate

    tm.send_message("alice", "Hello Alice!", msg_type="message", team_name="msg-team")

    assert inbox.exists(), "Inbox file should exist after sending message"
    content = inbox.read_text()
    assert "Hello Alice!" in content, f"Message content not found in inbox: {content}"

    inbox.unlink(missing_ok=True)
    print("PASS: test_send_message")
    return True


def test_check_inbox():
    tm = TeammateManager()
    tm.create_team("inbox-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="alice", team_name="inbox-team", inbox_path=inbox)
    tm._teams["inbox-team"]["alice"] = teammate

    tm.send_message("alice", "First message", msg_type="message", team_name="inbox-team")
    tm.send_message("alice", "Second message", msg_type="message", team_name="inbox-team")

    msgs = tm.check_inbox("alice", "inbox-team")
    assert len(msgs) == 2, f"Expected 2 messages, got {len(msgs)}"
    assert msgs[0]["content"] == "First message", f"First message mismatch: {msgs[0]}"
    assert msgs[1]["content"] == "Second message", f"Second message mismatch: {msgs[1]}"

    msgs_after = tm.check_inbox("alice", "inbox-team")
    assert len(msgs_after) == 0, f"Inbox should be empty after check, got {len(msgs_after)} messages"

    inbox.unlink(missing_ok=True)
    print("PASS: test_check_inbox")
    return True


def test_message_types():
    tm = TeammateManager()
    tm.create_team("types-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="alice", team_name="types-team", inbox_path=inbox)
    tm._teams["types-team"]["alice"] = teammate

    for msg_type in ["message", "broadcast", "shutdown_request"]:
        tm.send_message("alice", f"Content for {msg_type}", msg_type=msg_type, team_name="types-team")

    msgs = tm.check_inbox("alice", "types-team")
    assert len(msgs) == 3, f"Expected 3 messages, got {len(msgs)}"
    types_received = [m["type"] for m in msgs]
    assert "message" in types_received, f"Missing 'message' type in {types_received}"
    assert "broadcast" in types_received, f"Missing 'broadcast' type in {types_received}"
    assert "shutdown_request" in types_received, f"Missing 'shutdown_request' type in {types_received}"

    inbox.unlink(missing_ok=True)
    print("PASS: test_message_types")
    return True


def test_team_status():
    tm = TeammateManager()
    tm.create_team("status-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="bob", team_name="status-team", inbox_path=inbox)
    tm._teams["status-team"]["bob"] = teammate

    status = tm.get_team_status("status-team")
    assert "status-team" in status, f"Team name should be in status, got: {status}"
    assert "bob" in status, f"Member name should be in status, got: {status}"

    inbox.unlink(missing_ok=True)
    print("PASS: test_team_status")
    return True


def test_delete_team():
    tm = TeammateManager()
    tm.create_team("del-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="worker", team_name="del-team", inbox_path=inbox)
    tm._teams["del-team"]["worker"] = teammate

    result = tm.delete_team("del-team")
    assert "deleted" in result.lower(), f"Expected 'deleted' in response, got: {result}"
    assert "del-team" not in tm._teams, "Team should be removed from _teams"

    inbox.unlink(missing_ok=True)
    print("PASS: test_delete_team")
    return True


def test_task_claiming_logic():
    with tempfile.TemporaryDirectory() as tmpdir:
        task_mgr = TaskManager(Path(tmpdir))
        task_mgr.create("Unblocked task A")
        task_mgr.create("Unblocked task B")
        task_mgr.create("Blocked task C")

        task_mgr.update("3", addBlockedBy=["1"])

        all_tasks = task_mgr.list_all()
        unclaimed_unblocked = [
            t for t in all_tasks
            if t.status == "pending" and not t.owner and not t.blocked_by
        ]
        assert len(unclaimed_unblocked) == 2, \
            f"Expected 2 unclaimed unblocked tasks, got {len(unclaimed_unblocked)}"

        subjects = [t.subject for t in unclaimed_unblocked]
        assert "Unblocked task A" in subjects, "Task A should be unclaimed and unblocked"
        assert "Unblocked task B" in subjects, "Task B should be unclaimed and unblocked"
    print("PASS: test_task_claiming_logic")
    return True


def test_task_claim_and_unblock():
    with tempfile.TemporaryDirectory() as tmpdir:
        task_mgr = TaskManager(Path(tmpdir))
        task_mgr.create("First step")
        task_mgr.create("Second step")
        task_mgr.create("Dependent step")

        task_mgr.update("3", addBlockedBy=["1"])

        task_mgr.update("1", status="in_progress", owner="alice")
        t1 = task_mgr.get("1")
        assert t1.owner == "alice", f"Expected owner 'alice', got '{t1.owner}'"

        task_mgr.update("1", status="completed")
        t3 = task_mgr.get("3")
        assert "1" not in t3.blocked_by, \
            f"Completing task 1 should unblock task 3, got blocked_by={t3.blocked_by}"
    print("PASS: test_task_claim_and_unblock")
    return True


def test_task_manager_with_owner():
    with tempfile.TemporaryDirectory() as tmpdir:
        task_mgr = TaskManager(Path(tmpdir))
        task_mgr.create("Owned task")
        task_mgr.update("1", owner="bob")

        task = task_mgr.get("1")
        assert task.owner == "bob", f"Expected owner 'bob', got '{task.owner}'"

        task_mgr2 = TaskManager(Path(tmpdir))
        task_reloaded = task_mgr2.get("1")
        assert task_reloaded.owner == "bob", \
            f"Owner should persist after reload, got '{task_reloaded.owner}'"
    print("PASS: test_task_manager_with_owner")
    return True


def test_multiple_message_types():
    tm = TeammateManager()
    tm.create_team("alltype-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="tester", team_name="alltype-team", inbox_path=inbox)
    tm._teams["alltype-team"]["tester"] = teammate

    all_types = ["message", "broadcast", "shutdown_request",
                 "shutdown_response", "plan_approval_response"]
    for msg_type in all_types:
        tm.send_message("tester", f"Content for {msg_type}",
                        msg_type=msg_type, team_name="alltype-team")

    msgs = tm.check_inbox("tester", "alltype-team")
    assert len(msgs) == 5, f"Expected 5 messages, got {len(msgs)}"

    received_types = [m["type"] for m in msgs]
    for expected_type in all_types:
        assert expected_type in received_types, \
            f"Missing type '{expected_type}' in received: {received_types}"

    for msg in msgs:
        expected_content = f"Content for {msg['type']}"
        assert msg["content"] == expected_content, \
            f"Content mismatch for type '{msg['type']}': got '{msg['content']}'"

    inbox.unlink(missing_ok=True)
    print("PASS: test_multiple_message_types")
    return True


def test_shutdown_via_delete():
    tm = TeammateManager()
    tm.create_team("shutdown-team")

    inbox_a = Path(tempfile.mktemp(suffix=".jsonl"))
    inbox_b = Path(tempfile.mktemp(suffix=".jsonl"))
    mate_a = Teammate(name="alpha", team_name="shutdown-team", inbox_path=inbox_a)
    mate_b = Teammate(name="beta", team_name="shutdown-team", inbox_path=inbox_b)
    tm._teams["shutdown-team"]["alpha"] = mate_a
    tm._teams["shutdown-team"]["beta"] = mate_b

    result = tm.delete_team("shutdown-team")
    assert "deleted" in result.lower(), f"Expected 'deleted' in response, got: {result}"
    assert "shutdown-team" not in tm._teams, "Team should be removed from _teams"

    assert mate_a.status == "shutdown", \
        f"Teammate alpha status should be 'shutdown', got '{mate_a.status}'"
    assert mate_b.status == "shutdown", \
        f"Teammate beta status should be 'shutdown', got '{mate_b.status}'"

    for inbox, name in [(inbox_a, "alpha"), (inbox_b, "beta")]:
        assert inbox.exists(), f"Inbox for {name} should exist"
        msgs = []
        with open(inbox, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    msgs.append(json.loads(line))
        shutdown_msgs = [m for m in msgs if m.get("type") == "shutdown_request"]
        assert len(shutdown_msgs) >= 1, \
            f"Expected at least 1 shutdown_request in {name}'s inbox, got {len(shutdown_msgs)}"

    inbox_a.unlink(missing_ok=True)
    inbox_b.unlink(missing_ok=True)
    print("PASS: test_shutdown_via_delete")
    return True


def test_task_board_sharing():
    with tempfile.TemporaryDirectory() as tmpdir:
        tm1 = TaskManager(Path(tmpdir))
        tm2 = TaskManager(Path(tmpdir))

        tm1.create("Shared task")

        tasks_from_tm2 = tm2.list_all()
        assert len(tasks_from_tm2) == 1, \
            f"tm2 should see 1 task, got {len(tasks_from_tm2)}"
        assert tasks_from_tm2[0].subject == "Shared task", \
            f"Subject mismatch: got '{tasks_from_tm2[0].subject}'"

        task_from_tm2 = tm2.get("1")
        assert task_from_tm2 is not None, "tm2 should be able to get task by ID"
        assert task_from_tm2.subject == "Shared task", \
            f"tm2.get subject mismatch: got '{task_from_tm2.subject}'"

        tm1.update("1", owner="frontend-agent")

        task_updated = tm2.get("1")
        assert task_updated.owner == "frontend-agent", \
            f"tm2 should see updated owner 'frontend-agent', got '{task_updated.owner}'"
    print("PASS: test_task_board_sharing")
    return True


# =============================================================================
# Broadcast and TEAMMATE_TOOLS Tests
# =============================================================================

def test_broadcast_sends_to_all():
    tm = TeammateManager()
    tm.create_team("bcast-team")

    inboxes = []
    names = ["alice", "bob", "carol"]
    for name in names:
        inbox = Path(tempfile.mktemp(suffix=".jsonl"))
        teammate = Teammate(name=name, team_name="bcast-team", inbox_path=inbox)
        tm._teams["bcast-team"][name] = teammate
        inboxes.append(inbox)

    # broadcast is done via send_message with msg_type="broadcast"
    tm.send_message("", "All hands meeting at 3pm",
                    msg_type="broadcast", sender="lead", team_name="bcast-team")

    for i, name in enumerate(names):
        msgs = tm.check_inbox(name, "bcast-team")
        assert len(msgs) >= 1, \
            f"Expected at least 1 message in {name}'s inbox, got {len(msgs)}"
        broadcast_msgs = [m for m in msgs if m.get("type") == "broadcast"]
        assert len(broadcast_msgs) >= 1, \
            f"Expected at least 1 broadcast in {name}'s inbox, got {len(broadcast_msgs)}"
        assert "All hands meeting at 3pm" in broadcast_msgs[0]["content"], \
            f"Broadcast content mismatch for {name}: {broadcast_msgs[0]['content']}"

    for inbox in inboxes:
        inbox.unlink(missing_ok=True)
    print("PASS: test_broadcast_sends_to_all")
    return True


def test_teammate_tools_include_tasks():
    from v8_team_agent import TEAMMATE_TOOLS
    tool_names = [t["name"] for t in TEAMMATE_TOOLS]

    expected = ["TaskCreate", "TaskUpdate", "TaskList", "SendMessage"]
    for name in expected:
        assert name in tool_names, \
            f"TEAMMATE_TOOLS should include '{name}', got: {tool_names}"
    print("PASS: test_teammate_tools_include_tasks")
    return True


def test_v8_tools_in_all_tools():
    from v8_team_agent import ALL_TOOLS
    tool_names = {t["name"] for t in ALL_TOOLS}
    assert "TeamCreate" in tool_names, "ALL_TOOLS should include TeamCreate"
    assert "SendMessage" in tool_names, "ALL_TOOLS should include SendMessage"
    assert "TeamDelete" in tool_names, "ALL_TOOLS should include TeamDelete"
    assert "TaskOutput" in tool_names, "ALL_TOOLS should include TaskOutput (from v7)"
    assert "TaskStop" in tool_names, "ALL_TOOLS should include TaskStop (from v7)"
    print("PASS: test_v8_tools_in_all_tools")
    return True


# =============================================================================
# v8 Mechanism-Specific Tests
# =============================================================================

def test_v8_tool_count():
    """Verify v8 has exactly 15 tools (v7's 12 + TeamCreate + SendMessage + TeamDelete)."""
    from v8_team_agent import ALL_TOOLS
    assert len(ALL_TOOLS) == 15, f"v8 should have 15 tools, got {len(ALL_TOOLS)}"
    print("PASS: test_v8_tool_count")
    return True


def test_v8_teammate_tools_subset():
    """Verify TEAMMATE_TOOLS is a subset of ALL_TOOLS (teammates get fewer tools).

    Teammates get BASE_TOOLS + task CRUD + SendMessage, but NOT the full
    lead toolset (no TeamCreate, TeamDelete, TaskOutput, TaskStop).
    """
    from v8_team_agent import TEAMMATE_TOOLS, ALL_TOOLS
    teammate_names = {t["name"] for t in TEAMMATE_TOOLS}
    all_names = {t["name"] for t in ALL_TOOLS}

    assert teammate_names.issubset(all_names), \
        f"TEAMMATE_TOOLS should be subset of ALL_TOOLS. Extra: {teammate_names - all_names}"
    assert len(TEAMMATE_TOOLS) < len(ALL_TOOLS), \
        "TEAMMATE_TOOLS should have fewer tools than ALL_TOOLS"
    assert "TeamCreate" not in teammate_names, \
        "Teammates should NOT have TeamCreate (only the lead does)"
    assert "TeamDelete" not in teammate_names, \
        "Teammates should NOT have TeamDelete (only the lead does)"

    print("PASS: test_v8_teammate_tools_subset")
    return True


def test_v8_message_types_constant():
    """Verify MESSAGE_TYPES includes all 5 required types."""
    from v8_team_agent import TeammateManager
    expected = {"message", "broadcast", "shutdown_request",
                "shutdown_response", "plan_approval_response"}
    assert TeammateManager.MESSAGE_TYPES == expected, \
        f"MESSAGE_TYPES should be {expected}, got {TeammateManager.MESSAGE_TYPES}"
    print("PASS: test_v8_message_types_constant")
    return True


def test_v8_teammate_status_lifecycle():
    """Verify Teammate dataclass starts as 'active' and changes to 'shutdown'."""
    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    t = Teammate(name="test", team_name="test-team", inbox_path=inbox)

    assert t.status == "active", f"Initial status should be 'active', got '{t.status}'"

    t.status = "idle"
    assert t.status == "idle", f"Status should change to 'idle', got '{t.status}'"

    t.status = "shutdown"
    assert t.status == "shutdown", f"Status should change to 'shutdown', got '{t.status}'"

    inbox.unlink(missing_ok=True)
    print("PASS: test_v8_teammate_status_lifecycle")
    return True


def test_v8_inbox_jsonl_format():
    """Verify inbox uses JSONL format (one JSON object per line)."""
    tm = TeammateManager()
    tm.create_team("jsonl-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="jsonl-test", team_name="jsonl-team", inbox_path=inbox)
    tm._teams["jsonl-team"]["jsonl-test"] = teammate

    tm.send_message("jsonl-test", "Message 1", msg_type="message", team_name="jsonl-team")
    tm.send_message("jsonl-test", "Message 2", msg_type="broadcast", team_name="jsonl-team")

    with open(inbox) as f:
        lines = [l.strip() for l in f if l.strip()]

    assert len(lines) == 2, f"Expected 2 JSONL lines, got {len(lines)}"
    for i, line in enumerate(lines):
        try:
            data = json.loads(line)
            assert "type" in data, f"Line {i}: missing 'type' field"
            assert "content" in data, f"Line {i}: missing 'content' field"
        except json.JSONDecodeError:
            raise AssertionError(f"Line {i} is not valid JSON: {line[:100]}")

    inbox.unlink(missing_ok=True)
    print("PASS: test_v8_inbox_jsonl_format")
    return True


def test_v8_agent_loop_structure():
    """Verify v8 agent_loop has the notification drain pattern.

    The lead agent loop should drain background notifications before
    each API call and inject them as user messages.
    """
    import inspect, v8_team_agent

    source = open(v8_team_agent.__file__).read()
    has_notifications = "drain_notifications" in source
    has_agent_loop = "def agent_loop" in source
    assert has_notifications, "v8 code must have drain_notifications for notification bus"
    assert has_agent_loop, "v8 code must have agent_loop function"

    print("PASS: test_v8_agent_loop_structure")
    return True


def test_v8_teams_dir_path():
    """Verify TEAMS_DIR is defined for file-based inbox persistence."""
    from v8_team_agent import TEAMS_DIR
    assert TEAMS_DIR is not None, "TEAMS_DIR must be defined"
    assert "teams" in str(TEAMS_DIR).lower(), \
        f"TEAMS_DIR should contain 'teams', got: {TEAMS_DIR}"
    print("PASS: test_v8_teams_dir_path")
    return True


def test_v8_teammate_bg_prefix():
    """Verify v8's BackgroundManager maps 'teammate' type to 't' prefix.

    v8 extends v7's prefix scheme: b=bash, a=agent, t=teammate.
    This is how the notification system distinguishes task types.
    """
    from v8_team_agent import BackgroundManager
    bm = BackgroundManager()
    tid = bm.run_in_background(lambda: "teammate result", task_type="teammate")
    assert tid.startswith("t"), f"Teammate task should start with 't', got '{tid[0]}'"
    bm.get_output(tid, block=True, timeout=2000)
    print("PASS: test_v8_teammate_bg_prefix")
    return True


def test_spawn_teammate_error_no_team():
    """Verify spawn_teammate returns error for non-existent team."""
    tm = TeammateManager()
    result = tm.spawn_teammate("worker", "ghost-team", "do stuff")
    assert "error" in result.lower(), \
        f"Should return error for non-existent team, got: {result}"
    print("PASS: test_spawn_teammate_error_no_team")
    return True


def test_spawn_teammate_returns_json():
    """Verify spawn_teammate returns JSON with name, team, status."""
    tm = TeammateManager()
    tm.create_team("spawn-test")
    result = tm.spawn_teammate("w1", "spawn-test", "test prompt")
    data = json.loads(result)
    assert data["name"] == "w1", f"Expected name 'w1', got '{data['name']}'"
    assert data["team"] == "spawn-test", f"Expected team 'spawn-test', got '{data['team']}'"
    assert data["status"] == "active", f"Expected status 'active', got '{data['status']}'"
    tm.delete_team("spawn-test")
    time.sleep(0.1)
    print("PASS: test_spawn_teammate_returns_json")
    return True


def test_find_teammate_cross_team():
    """Verify _find_teammate searches across all teams when team_name is None."""
    tm = TeammateManager()
    tm.create_team("alpha")
    tm.create_team("beta")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    mate = Teammate(name="hidden-worker", team_name="beta", inbox_path=inbox)
    tm._teams["beta"]["hidden-worker"] = mate

    found = tm._find_teammate("hidden-worker")
    assert found is not None, "Should find teammate by name across all teams"
    assert found.team_name == "beta"

    found_direct = tm._find_teammate("hidden-worker", "beta")
    assert found_direct is not None, "Should find with explicit team_name"

    not_found = tm._find_teammate("nonexistent", "alpha")
    assert not_found is None, "Should not find nonexistent teammate"

    inbox.unlink(missing_ok=True)
    print("PASS: test_find_teammate_cross_team")
    return True


def test_teammate_loop_has_tool_loop():
    """Verify _teammate_loop contains the tool execution loop structure.

    v8_team_agent uses a simplified loop: process prompt, execute tools until
    the model stops calling tools, then shutdown. No idle phase.
    """
    import inspect
    source = inspect.getsource(TeammateManager._teammate_loop)

    assert "tool_use" in source, "Loop must check for tool_use stop reason"
    assert "tool_calls" in source or "tool_use" in source, "Loop must handle tool calls"
    assert "shutdown" in source, "Loop must handle shutdown"
    assert "microcompact" in source, "Loop must support context compression"

    print("PASS: test_teammate_loop_has_tool_loop")
    return True


def test_teammate_loop_context_compression():
    """Verify _teammate_loop supports context compression via microcompact/auto_compact.

    In the new simplified loop, the teammate still uses context compression
    but does not re-inject identity or pick up unclaimed tasks.
    """
    import inspect
    source = inspect.getsource(TeammateManager._teammate_loop)

    assert "auto_compact" in source or "microcompact" in source, \
        "Loop must support context compression"
    assert "should_compact" in source or "microcompact" in source, \
        "Loop must check whether to compact"

    print("PASS: test_teammate_loop_context_compression")
    return True


def test_teammate_loop_shutdown_on_done():
    """Verify _teammate_loop shuts down when the model stops calling tools.

    In the simplified v8_team_agent, the teammate processes its prompt,
    executes tools until stop_reason != tool_use, then terminates.
    """
    import inspect
    source = inspect.getsource(TeammateManager._teammate_loop)

    assert "shutdown" in source, \
        "Loop must set status to shutdown when done"
    assert "stop_reason" in source or "tool_use" in source, \
        "Loop must check stop_reason to decide when to exit"

    print("PASS: test_teammate_loop_shutdown_on_done")
    return True


def test_broadcast_excludes_sender():
    """Verify broadcast does not send message back to the sender."""
    tm = TeammateManager()
    tm.create_team("excl-team")

    inboxes = {}
    for name in ["lead", "worker1", "worker2"]:
        inbox = Path(tempfile.mktemp(suffix=".jsonl"))
        mate = Teammate(name=name, team_name="excl-team", inbox_path=inbox)
        tm._teams["excl-team"][name] = mate
        inboxes[name] = inbox

    tm.send_message("", "Announcement", msg_type="broadcast",
                    sender="lead", team_name="excl-team")

    lead_msgs = tm.check_inbox("lead", "excl-team")
    w1_msgs = tm.check_inbox("worker1", "excl-team")
    w2_msgs = tm.check_inbox("worker2", "excl-team")

    assert len(lead_msgs) == 0, \
        f"Sender ('lead') should NOT receive own broadcast, got {len(lead_msgs)} msgs"
    assert len(w1_msgs) >= 1, "worker1 should receive broadcast"
    assert len(w2_msgs) >= 1, "worker2 should receive broadcast"

    for inbox in inboxes.values():
        inbox.unlink(missing_ok=True)
    print("PASS: test_broadcast_excludes_sender")
    return True


# =============================================================================
# v8 New Mechanism Tests (from final_design.md)
# =============================================================================


def test_config_json_created():
    """Create team, verify config.json exists with correct structure."""
    import tempfile
    import v8_team_agent
    orig_dir = v8_team_agent.TEAMS_DIR
    with tempfile.TemporaryDirectory() as tmpdir:
        v8_team_agent.TEAMS_DIR = Path(tmpdir)
        tm = TeammateManager()
        tm.create_team("cfg-test")
        config_path = Path(tmpdir) / "cfg-test" / "config.json"
        assert config_path.exists(), "config.json should exist after create_team"
        data = json.loads(config_path.read_text())
        assert "name" in data, "config.json should have name"
        assert data["name"] == "cfg-test"
        v8_team_agent.TEAMS_DIR = orig_dir
    print("PASS: test_config_json_created")
    return True


def test_agent_id_format():
    """Spawn teammate, verify ID is '{name}@{team}'."""
    tm = TeammateManager()
    tm.create_team("id-test")
    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    mate = Teammate(name="alice", team_name="id-test", inbox_path=inbox)
    assert mate.agent_id == "alice@id-test", \
        f"Expected 'alice@id-test', got '{mate.agent_id}'"
    inbox.unlink(missing_ok=True)
    print("PASS: test_agent_id_format")
    return True


def test_teammate_colors_cycle():
    """Spawn 7 teammates, verify colors cycle from array."""
    from v8_team_agent import TEAMMATE_COLORS
    tm = TeammateManager()
    tm.create_team("color-test")
    inboxes = []
    colors_seen = []
    for i in range(7):
        inbox = Path(tempfile.mktemp(suffix=".jsonl"))
        name = f"worker{i}"
        color_idx = i % len(TEAMMATE_COLORS)
        mate = Teammate(name=name, team_name="color-test", inbox_path=inbox,
                        color=TEAMMATE_COLORS[color_idx])
        tm._teams["color-test"][name] = mate
        colors_seen.append(mate.color)
        inboxes.append(inbox)
    # Colors should cycle
    assert colors_seen[0] == colors_seen[5], \
        "Color at index 0 should equal color at index 5 (cycling)"
    assert colors_seen[1] == colors_seen[6], \
        "Color at index 1 should equal color at index 6 (cycling)"
    for inbox in inboxes:
        inbox.unlink(missing_ok=True)
    print("PASS: test_teammate_colors_cycle")
    return True


def test_teammate_tools_includes_task_get():
    """Verify TEAMMATE_TOOLS contains TaskGet."""
    from v8_team_agent import TEAMMATE_TOOLS
    tool_names = {t["name"] for t in TEAMMATE_TOOLS}
    assert "TaskGet" in tool_names, "TEAMMATE_TOOLS must include TaskGet"
    print("PASS: test_teammate_tools_includes_task_get")
    return True


def test_broadcast_no_recipient():
    """Send broadcast, verify no recipient required."""
    tm = TeammateManager()
    tm.create_team("bcast-nr")
    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    mate = Teammate(name="recv", team_name="bcast-nr", inbox_path=inbox)
    tm._teams["bcast-nr"]["recv"] = mate
    # recipient="" is valid for broadcast
    result = tm.send_message("", "Hello all", msg_type="broadcast",
                             sender="lead", team_name="bcast-nr")
    assert "error" not in result.lower(), \
        f"Broadcast with empty recipient should succeed, got: {result}"
    msgs = tm.check_inbox("recv", "bcast-nr")
    assert len(msgs) >= 1, "Recipient should have received broadcast"
    inbox.unlink(missing_ok=True)
    print("PASS: test_broadcast_no_recipient")
    return True


def test_message_requires_recipient():
    """Send message without valid recipient, verify error."""
    tm = TeammateManager()
    tm.create_team("recip-test")
    result = tm.send_message("nonexistent", "Hello", msg_type="message",
                             team_name="recip-test")
    assert "error" in result.lower() or "not found" in result.lower(), \
        f"Message to nonexistent recipient should fail, got: {result}"
    print("PASS: test_message_requires_recipient")
    return True


def test_config_persists_after_spawn():
    """Create team, add teammate via _update_team_config, verify config.json has the member entry."""
    import v8_team_agent
    orig_dir = v8_team_agent.TEAMS_DIR
    with tempfile.TemporaryDirectory() as tmpdir:
        v8_team_agent.TEAMS_DIR = Path(tmpdir)
        tm = TeammateManager()
        tm.create_team("persist-test")

        inbox = Path(tempfile.mktemp(suffix=".jsonl"))
        mate = Teammate(name="alice", team_name="persist-test", inbox_path=inbox)
        tm._teams["persist-test"]["alice"] = mate
        tm._update_team_config("persist-test")

        config_path = Path(tmpdir) / "persist-test" / "config.json"
        assert config_path.exists(), "config.json should exist after adding teammate"
        data = json.loads(config_path.read_text())
        member_names = [m["name"] for m in data.get("members", [])]
        assert "alice" in member_names, \
            f"config.json should list 'alice' as member, got {member_names}"

        inbox.unlink(missing_ok=True)
        v8_team_agent.TEAMS_DIR = orig_dir
    print("PASS: test_config_persists_after_spawn")
    return True


def test_config_recovers_after_teammate_shutdown():
    """Add teammate, then remove, verify config.json reflects the removal."""
    import v8_team_agent
    orig_dir = v8_team_agent.TEAMS_DIR
    with tempfile.TemporaryDirectory() as tmpdir:
        v8_team_agent.TEAMS_DIR = Path(tmpdir)
        tm = TeammateManager()
        tm.create_team("remove-test")

        inbox = Path(tempfile.mktemp(suffix=".jsonl"))
        mate = Teammate(name="bob", team_name="remove-test", inbox_path=inbox)
        tm._teams["remove-test"]["bob"] = mate
        tm._update_team_config("remove-test")

        config_path = Path(tmpdir) / "remove-test" / "config.json"
        data = json.loads(config_path.read_text())
        assert len(data["members"]) == 1, "Should have 1 member"

        del tm._teams["remove-test"]["bob"]
        tm._update_team_config("remove-test")

        data = json.loads(config_path.read_text())
        assert len(data["members"]) == 0, \
            f"After removal, members should be empty, got {data['members']}"

        inbox.unlink(missing_ok=True)
        v8_team_agent.TEAMS_DIR = orig_dir
    print("PASS: test_config_recovers_after_teammate_shutdown")
    return True


def test_broadcast_to_many_teammates():
    """Create team with 5+ teammates, broadcast, verify all receive (excluding sender)."""
    tm = TeammateManager()
    tm.create_team("big-bcast")

    inboxes = []
    names = ["sender"] + [f"worker{i}" for i in range(5)]
    for name in names:
        inbox = Path(tempfile.mktemp(suffix=".jsonl"))
        mate = Teammate(name=name, team_name="big-bcast", inbox_path=inbox)
        tm._teams["big-bcast"][name] = mate
        inboxes.append(inbox)

    tm.send_message("", "Team update", msg_type="broadcast",
                    sender="sender", team_name="big-bcast")

    sender_msgs = tm.check_inbox("sender", "big-bcast")
    assert len(sender_msgs) == 0, \
        f"Sender should not receive broadcast, got {len(sender_msgs)} msgs"

    for i in range(5):
        name = f"worker{i}"
        msgs = tm.check_inbox(name, "big-bcast")
        assert len(msgs) == 1, \
            f"{name} should have received 1 broadcast, got {len(msgs)}"
        assert "Team update" in msgs[0]["content"], \
            f"{name} broadcast content mismatch"

    for inbox in inboxes:
        inbox.unlink(missing_ok=True)
    print("PASS: test_broadcast_to_many_teammates")
    return True


def test_broadcast_with_no_teammates():
    """Empty team (only sender), broadcast, verify count=0."""
    tm = TeammateManager()
    tm.create_team("empty-bcast")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    mate = Teammate(name="lonely", team_name="empty-bcast", inbox_path=inbox)
    tm._teams["empty-bcast"]["lonely"] = mate

    result = tm.send_message("", "Nobody here", msg_type="broadcast",
                             sender="lonely", team_name="empty-bcast")
    assert "0" in result, \
        f"Broadcast with only sender should reach 0 teammates, got: {result}"

    msgs = tm.check_inbox("lonely", "empty-bcast")
    assert len(msgs) == 0, \
        f"Sender should not receive own broadcast, got {len(msgs)} msgs"

    inbox.unlink(missing_ok=True)
    print("PASS: test_broadcast_with_no_teammates")
    return True


def test_multi_owner_race_condition():
    """Two threads simultaneously try to update same task's owner, verify consistency."""
    import threading

    with tempfile.TemporaryDirectory() as tmpdir:
        task_mgr = TaskManager(Path(tmpdir))
        task_mgr.create("Race condition task")

        errors = []
        results = []

        def claim_task(owner_name):
            try:
                task_mgr.update("1", owner=owner_name)
                task = task_mgr.get("1")
                results.append(task.owner)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=claim_task, args=("alice",))
        t2 = threading.Thread(target=claim_task, args=("bob",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0, f"Race condition errors: {errors}"

        final_task = task_mgr.get("1")
        assert final_task.owner in ("alice", "bob"), \
            f"Final owner should be 'alice' or 'bob', got '{final_task.owner}'"

    print("PASS: test_multi_owner_race_condition")
    return True


def test_check_inbox_atomicity():
    """Verify check_inbox uses lock file to prevent race with _write_to_inbox."""
    tm = TeammateManager()
    tm.create_team("lock-team")

    inbox = Path(tempfile.mktemp(suffix=".jsonl"))
    teammate = Teammate(name="locker", team_name="lock-team", inbox_path=inbox)
    tm._teams["lock-team"]["locker"] = teammate

    # Write initial message
    tm.send_message("locker", "msg1", msg_type="message", team_name="lock-team")

    # Hold lock to simulate contention
    lock_path = inbox.with_suffix(".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        # check_inbox should return empty (cannot acquire lock)
        msgs = tm.check_inbox("locker", "lock-team")
        assert msgs == [], "check_inbox should return empty when lock is held"
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)

    # Now without lock, should get the message
    msgs = tm.check_inbox("locker", "lock-team")
    assert len(msgs) == 1, f"check_inbox should return message when lock is free, got {len(msgs)}"

    inbox.unlink(missing_ok=True)
    print("PASS: test_check_inbox_atomicity")
    return True


# =============================================================================
# LLM Integration Tests
# =============================================================================

from tests.helpers import TASK_OUTPUT_TOOL, TASK_STOP_TOOL
from tests.helpers import TEAM_CREATE_TOOL, SEND_MESSAGE_TOOL, TEAM_DELETE_TOOL

V8_TOOLS = [BASH_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL, EDIT_FILE_TOOL,
            TASK_CREATE_TOOL, TASK_LIST_TOOL, TASK_UPDATE_TOOL,
            TASK_OUTPUT_TOOL, TASK_STOP_TOOL,
            TEAM_CREATE_TOOL, SEND_MESSAGE_TOOL, TEAM_DELETE_TOOL]


def test_llm_creates_team():
    """LLM uses TeamCreate to set up a new team.

    v8's key mechanism: model creates a team for coordination.
    """
    client = get_client()
    if not client:
        print("SKIP: No API key")
        return True

    text, calls, _ = run_agent(
        client,
        "Create a new team called 'frontend-team' for building the UI. Use the TeamCreate tool.",
        V8_TOOLS,
        system="You are a team lead. Use TeamCreate to set up teams for collaboration.",
    )

    team_calls = [c for c in calls if c[0] == "TeamCreate"]
    assert len(team_calls) >= 1, \
        f"Model should use TeamCreate, got: {[c[0] for c in calls]}"
    assert "frontend" in team_calls[0][1].get("team_name", "").lower(), \
        f"Team name should contain 'frontend', got: {team_calls[0][1]}"

    print(f"Tool calls: {len(calls)}, TeamCreate: {len(team_calls)}")
    print("PASS: test_llm_creates_team")
    return True


def test_llm_sends_message():
    """LLM uses SendMessage to communicate with a teammate."""
    client = get_client()
    if not client:
        print("SKIP: No API key")
        return True

    text, calls, _ = run_agent(
        client,
        "You MUST call the SendMessage tool right now with these parameters: "
        "type='message', recipient='alice', content='Please review the API code'. "
        "Do NOT respond with text. Just call the SendMessage tool.",
        V8_TOOLS,
        system="You are a team lead. You MUST use the SendMessage tool when asked. Always use tools first.",
    )

    msg_calls = [c for c in calls if c[0] == "SendMessage"]
    assert len(msg_calls) >= 1, \
        f"Model should use SendMessage, got: {[c[0] for c in calls]}"

    print(f"Tool calls: {len(calls)}, SendMessage: {len(msg_calls)}")
    print("PASS: test_llm_sends_message")
    return True


def test_llm_broadcasts_message():
    """LLM uses SendMessage with type='broadcast' to reach all teammates."""
    client = get_client()
    if not client:
        print("SKIP: No API key")
        return True

    text, calls, _ = run_agent(
        client,
        "Broadcast a message to all teammates: 'Stop all work, critical bug found'. "
        "Use SendMessage with type='broadcast'.",
        V8_TOOLS,
        system="You are a team lead. Use SendMessage with type='broadcast' to reach all teammates.",
    )

    msg_calls = [c for c in calls if c[0] == "SendMessage"]
    assert len(msg_calls) >= 1, \
        f"Model should use SendMessage, got: {[c[0] for c in calls]}"
    assert msg_calls[0][1].get("type") == "broadcast", \
        f"Should use broadcast type, got: {msg_calls[0][1].get('type')}"

    print(f"Tool calls: {len(calls)}, SendMessage: {len(msg_calls)}")
    print("PASS: test_llm_broadcasts_message")
    return True


def test_llm_team_workflow():
    """LLM creates team, sends message, then cleans up -- full lifecycle."""
    client = get_client()
    if not client:
        print("SKIP: No API key")
        return True

    text, calls, _ = run_agent(
        client,
        "Do the following in order:\n"
        "1) Create a team called 'build-team' using TeamCreate\n"
        "2) Send a message to 'bob' saying 'Start the build' using SendMessage\n"
        "3) Delete the team using TeamDelete\n"
        "Execute all three steps.",
        V8_TOOLS,
        system="You are a team lead. Use TeamCreate, SendMessage, and TeamDelete.",
        max_turns=10,
    )

    tool_names = [c[0] for c in calls]
    assert "TeamCreate" in tool_names, f"Should use TeamCreate, got: {tool_names}"
    assert "SendMessage" in tool_names, f"Should use SendMessage, got: {tool_names}"
    assert "TeamDelete" in tool_names, f"Should use TeamDelete, got: {tool_names}"

    if "TeamCreate" in tool_names and "TeamDelete" in tool_names:
        create_idx = tool_names.index("TeamCreate")
        delete_idx = tool_names.index("TeamDelete")
        assert create_idx < delete_idx, \
            "TeamCreate should come before TeamDelete"

    print(f"Tool calls: {len(calls)}")
    print("PASS: test_llm_team_workflow")
    return True


def test_llm_shutdown_request():
    """LLM uses SendMessage with type='shutdown_request' to shut down a teammate."""
    client = get_client()
    if not client:
        print("SKIP: No API key")
        return True

    text, calls, _ = run_agent(
        client,
        "You MUST call the SendMessage tool with these exact parameters: "
        "type='shutdown_request', recipient='worker-1', content='Shutting down'. "
        "Do NOT respond with text. Just call the tool.",
        V8_TOOLS,
        system="You MUST use the SendMessage tool when asked. Always call tools immediately.",
    )

    msg_calls = [c for c in calls if c[0] == "SendMessage"]
    assert len(msg_calls) >= 1, \
        f"Model should use SendMessage, got: {[c[0] for c in calls]}"

    print(f"Tool calls: {len(calls)}, SendMessage: {len(msg_calls)}")
    print("PASS: test_llm_shutdown_request")
    return True


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    sys.exit(0 if run_tests([
        # TeammateManager unit tests
        test_create_team,
        test_create_duplicate_team,
        test_send_message,
        test_check_inbox,
        test_message_types,
        test_team_status,
        test_delete_team,
        test_task_claiming_logic,
        test_task_claim_and_unblock,
        test_task_manager_with_owner,
        test_multiple_message_types,
        test_shutdown_via_delete,
        test_task_board_sharing,
        # Broadcast and tools tests
        test_broadcast_sends_to_all,
        test_teammate_tools_include_tasks,
        test_v8_tools_in_all_tools,
        # Mechanism-specific
        test_v8_tool_count,
        test_v8_teammate_tools_subset,
        test_v8_message_types_constant,
        test_v8_teammate_status_lifecycle,
        test_v8_inbox_jsonl_format,
        test_v8_agent_loop_structure,
        test_v8_teams_dir_path,
        test_v8_teammate_bg_prefix,
        test_spawn_teammate_error_no_team,
        test_spawn_teammate_returns_json,
        test_find_teammate_cross_team,
        test_teammate_loop_has_tool_loop,
        test_teammate_loop_context_compression,
        test_teammate_loop_shutdown_on_done,
        test_broadcast_excludes_sender,
        # v8 new mechanism tests
        test_config_json_created,
        test_agent_id_format,
        test_teammate_colors_cycle,
        test_teammate_tools_includes_task_get,
        test_broadcast_no_recipient,
        test_message_requires_recipient,
        test_config_persists_after_spawn,
        test_config_recovers_after_teammate_shutdown,
        test_broadcast_to_many_teammates,
        test_broadcast_with_no_teammates,
        test_multi_owner_race_condition,
        test_check_inbox_atomicity,
        # LLM integration tests
        test_llm_creates_team,
        test_llm_sends_message,
        test_llm_broadcasts_message,
        test_llm_team_workflow,
        test_llm_shutdown_request,
    ]) else 1)
