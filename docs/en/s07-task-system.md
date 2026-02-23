# s07: Tasks

> Tasks are persisted as JSON files with a dependency graph, so state survives context compression and can be shared across agents.

## Problem

In-memory state (for example the TodoManager from s03) is fragile under compression (s06). Once earlier turns are compacted into summaries, in-memory todo state is gone.

s06 -> s07 is the key transition:

1. Todo list state in memory is conversational and lossy.
2. Task board state on disk is durable and recoverable.

A second issue is visibility: in-memory structures are process-local, so teammates cannot reliably share that state.

## When to Use Task vs Todo

From s07 onward, Task is the default. Todo remains for short linear checklists.

## Quick Decision Matrix

| Situation | Prefer | Why |
|---|---|---|
| Short, single-session checklist | Todo | Lowest ceremony, fastest capture |
| Cross-session work, dependencies, or teammates | Task | Durable state, dependency graph, shared visibility |
| Unsure which one to use | Task | Easier to simplify later than migrate mid-run |

## Solution

```
.tasks/
  task_1.json  {"id":1, "status":"completed", ...}
  task_2.json  {"id":2, "blockedBy":[1], "status":"pending"}
  task_3.json  {"id":3, "blockedBy":[2], "status":"pending"}

Dependency resolution:
+----------+     +----------+     +----------+
| task 1   | --> | task 2   | --> | task 3   |
| complete |     | blocked  |     | blocked  |
+----------+     +----------+     +----------+
     |                ^
     +--- completing task 1 removes it from
          task 2's blockedBy list
```

## How It Works

1. TaskManager provides CRUD with one JSON file per task.

```python
class TaskManager:
    def create(self, subject: str, description: str = "") -> str:
        task = {
            "id": self._next_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blockedBy": [],
            "blocks": [],
            "owner": "",
        }
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2)
```

2. Completing a task clears that dependency from other tasks.

```python
def _clear_dependency(self, completed_id: int):
    for f in self.dir.glob("task_*.json"):
        task = json.loads(f.read_text())
        if completed_id in task.get("blockedBy", []):
            task["blockedBy"].remove(completed_id)
            self._save(task)
```

3. `update` handles status transitions and dependency wiring.

```python
def update(self, task_id, status=None,
           add_blocked_by=None, add_blocks=None):
    task = self._load(task_id)
    if status:
        task["status"] = status
        if status == "completed":
            self._clear_dependency(task_id)
    if add_blocks:
        task["blocks"] = list(set(task["blocks"] + add_blocks))
        for blocked_id in add_blocks:
            blocked = self._load(blocked_id)
            if task_id not in blocked["blockedBy"]:
                blocked["blockedBy"].append(task_id)
                self._save(blocked)
    self._save(task)
```

4. Task tools are added to the dispatch map.

```python
TOOL_HANDLERS = {
    # ...base tools...
    "task_create": lambda **kw: TASKS.create(kw["subject"]),
    "task_update": lambda **kw: TASKS.update(kw["task_id"],
                       kw.get("status")),
    "task_list":   lambda **kw: TASKS.list_all(),
    "task_get":    lambda **kw: TASKS.get(kw["task_id"]),
}
```

## Key Code

TaskManager with dependency graph (from `agents/s07_task_system.py`, lines 46-123):

```python
class TaskManager:
    def __init__(self, tasks_dir: Path):
        self.dir = tasks_dir
        self.dir.mkdir(exist_ok=True)
        self._next_id = self._max_id() + 1

    def _load(self, task_id: int) -> dict:
        path = self.dir / f"task_{task_id}.json"
        return json.loads(path.read_text())

    def _save(self, task: dict):
        path = self.dir / f"task_{task['id']}.json"
        path.write_text(json.dumps(task, indent=2))

    def create(self, subject, description=""):
        task = {"id": self._next_id, "subject": subject,
                "status": "pending", "blockedBy": [],
                "blocks": [], "owner": ""}
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2)

    def _clear_dependency(self, completed_id):
        for f in self.dir.glob("task_*.json"):
            task = json.loads(f.read_text())
            if completed_id in task.get("blockedBy", []):
                task["blockedBy"].remove(completed_id)
                self._save(task)
```

## What Changed From s06

| Component | Before (s06) | After (s07) |
|---|---|---|
| Tools | 5 | 8 (`task_create/update/list/get`) |
| State storage | In-memory only | JSON files in `.tasks/` |
| Dependencies | None | `blockedBy + blocks` graph |
| Persistence | Lost on compact | Survives compression |

## Design Rationale

File-based state survives compaction and process restarts. The dependency graph preserves execution order even when conversation details are forgotten. This turns transient chat context into durable work state.

Durability still needs a write discipline: reload task JSON before each write, validate expected `status/blockedBy`, then persist atomically. Otherwise concurrent writers can overwrite each other.

Course-level implication: s07+ defaults to Task because it better matches long-running and collaborative engineering workflows.

## Try It

```sh
cd learn-claude-code
python agents/s07_task_system.py
```

Suggested prompts:

1. `Create 3 tasks: "Setup project", "Write code", "Write tests". Make them depend on each other in order.`
2. `List all tasks and show the dependency graph`
3. `Complete task 1 and then list tasks to see task 2 unblocked`
4. `Create a task board for refactoring: parse -> transform -> emit -> test`
