# s12: Worktree + Task Isolation

> Isolate by directory, coordinate by task ID -- tasks are the control plane, worktrees are the execution plane, and an event stream makes every lifecycle step observable.

## The Problem

By s11, agents can claim and complete tasks autonomously. But every task runs in one shared directory. Ask two agents to refactor different modules at the same time and you hit three failure modes:

Agent A edits `auth.py`. Agent B edits `auth.py`. Neither knows the other touched it. Unstaged changes collide, task status says "in_progress" but the directory is a mess, and when something breaks there is no way to roll back one agent's work without destroying the other's. The task board tracks _what to do_ but has no opinion about _where to do it_.

The fix is to separate the two concerns. Tasks manage goals. Worktrees manage execution context. Bind them by task ID, and each agent gets its own directory, its own branch, and a clean teardown path.

## The Solution

```
Control Plane (.tasks/)              Execution Plane (.worktrees/)
+---------------------------+        +---------------------------+
| task_1.json               |        | index.json                |
|   id: 1                   |        |   name: "auth-refactor"   |
|   subject: "Auth refactor"|  bind  |   path: ".worktrees/..."  |
|   status: "in_progress"   | <----> |   branch: "wt/auth-..."   |
|   worktree: "auth-refactor"|       |   task_id: 1              |
+---------------------------+        |   status: "active"        |
                                     +---------------------------+
| task_2.json               |        |                           |
|   id: 2                   |  bind  |   name: "ui-login"        |
|   subject: "Login page"   | <----> |   task_id: 2              |
|   worktree: "ui-login"    |        |   status: "active"        |
+---------------------------+        +---------------------------+
                                               |
                                     +---------------------------+
                                     | events.jsonl (append-only)|
                                     | worktree.create.before    |
                                     | worktree.create.after     |
                                     | worktree.remove.after     |
                                     | task.completed            |
                                     +---------------------------+
```

Three state layers make this work:

1. **Control plane** (`.tasks/task_*.json`) -- what is assigned, in progress, or done. Key fields: `id`, `subject`, `status`, `owner`, `worktree`.
2. **Execution plane** (`.worktrees/index.json`) -- where commands run and whether the workspace is still valid. Key fields: `name`, `path`, `branch`, `task_id`, `status`.
3. **Runtime state** (in-memory) -- per-turn execution continuity: `current_task`, `current_worktree`, `tool_result`, `error`.

## How It Works

The lifecycle has five steps. Each step is a tool call.

1. **Create a task.** Persist the goal first. The task starts as `pending` with an empty `worktree` field.

```python
task = {
    "id": self._next_id,
    "subject": subject,
    "status": "pending",
    "owner": "",
    "worktree": "",
}
self._save(task)
```

2. **Create a worktree.** Allocate an isolated directory and branch. If you pass `task_id`, the task auto-advances to `in_progress` and the binding is written to both sides.

```python
self._run_git(["worktree", "add", "-b", branch, str(path), base_ref])

entry = {
    "name": name,
    "path": str(path),
    "branch": branch,
    "task_id": task_id,
    "status": "active",
}
idx["worktrees"].append(entry)
self._save_index(idx)

if task_id is not None:
    self.tasks.bind_worktree(task_id, name)
```

3. **Run commands in the worktree.** `worktree_run` sets `cwd` to the worktree path. Edits happen in the isolated directory, not the shared workspace.

```python
r = subprocess.run(
    command,
    shell=True,
    cwd=path,
    capture_output=True,
    text=True,
    timeout=300,
)
```

4. **Observe.** `worktree_status` shows git state inside the isolated context. `worktree_events` queries the append-only event stream.

5. **Close out.** Two choices:
   - `worktree_keep(name)` -- preserve the directory, mark lifecycle as `kept`.
   - `worktree_remove(name, complete_task=True)` -- remove the directory, complete the bound task, unbind, and emit `task.completed`. This is the closeout pattern: one call handles teardown and task completion together.

## State Machines

```
Task:     pending -------> in_progress -------> completed
               (worktree_create          (worktree_remove
                with task_id)        with complete_task=true)

Worktree: absent --------> active -----------> removed | kept
               (worktree_create)         (worktree_remove | worktree_keep)
```

## Key Code

The closeout pattern -- teardown + task completion in one operation (from `agents/s12_worktree_task_isolation.py`):

```python
def remove(self, name: str, force: bool = False, complete_task: bool = False) -> str:
    wt = self._find(name)
    if not wt:
        return f"Error: Unknown worktree '{name}'"

    self.events.emit(
        "worktree.remove.before",
        task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
        worktree={"name": name, "path": wt.get("path")},
    )
    try:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(wt["path"])
        self._run_git(args)

        if complete_task and wt.get("task_id") is not None:
            task_id = wt["task_id"]
            self.tasks.update(task_id, status="completed")
            self.tasks.unbind_worktree(task_id)
            self.events.emit("task.completed", task={
                "id": task_id, "status": "completed",
            }, worktree={"name": name})

        idx = self._load_index()
        for item in idx.get("worktrees", []):
            if item.get("name") == name:
                item["status"] = "removed"
                item["removed_at"] = time.time()
        self._save_index(idx)

        self.events.emit(
            "worktree.remove.after",
            task={"id": wt.get("task_id")} if wt.get("task_id") is not None else {},
            worktree={"name": name, "path": wt.get("path"), "status": "removed"},
        )
        return f"Removed worktree '{name}'"
    except Exception as e:
        self.events.emit(
            "worktree.remove.failed",
            worktree={"name": name},
            error=str(e),
        )
        raise
```

The task-side binding (from `agents/s12_worktree_task_isolation.py`):

```python
def bind_worktree(self, task_id: int, worktree: str, owner: str = "") -> str:
    task = self._load(task_id)
    task["worktree"] = worktree
    if task["status"] == "pending":
        task["status"] = "in_progress"
    task["updated_at"] = time.time()
    self._save(task)
```

The dispatch map wiring all tools together:

```python
TOOL_HANDLERS = {
    "bash":               lambda **kw: run_bash(kw["command"]),
    "read_file":          lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file":         lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":          lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "task_create":        lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),
    "task_list":          lambda **kw: TASKS.list_all(),
    "task_get":           lambda **kw: TASKS.get(kw["task_id"]),
    "task_update":        lambda **kw: TASKS.update(kw["task_id"], kw.get("status"), kw.get("owner")),
    "task_bind_worktree": lambda **kw: TASKS.bind_worktree(kw["task_id"], kw["worktree"]),
    "worktree_create":    lambda **kw: WORKTREES.create(kw["name"], kw.get("task_id")),
    "worktree_list":      lambda **kw: WORKTREES.list_all(),
    "worktree_status":    lambda **kw: WORKTREES.status(kw["name"]),
    "worktree_run":       lambda **kw: WORKTREES.run(kw["name"], kw["command"]),
    "worktree_keep":      lambda **kw: WORKTREES.keep(kw["name"]),
    "worktree_remove":    lambda **kw: WORKTREES.remove(kw["name"], kw.get("force", False), kw.get("complete_task", False)),
    "worktree_events":    lambda **kw: EVENTS.list_recent(kw.get("limit", 20)),
}
```

## Event Stream

Every lifecycle transition emits a before/after/failed triplet to `.worktrees/events.jsonl`. This is an append-only log, not a replacement for task/worktree state files.

Events emitted:

- `worktree.create.before` / `worktree.create.after` / `worktree.create.failed`
- `worktree.remove.before` / `worktree.remove.after` / `worktree.remove.failed`
- `worktree.keep`
- `task.completed` (when `complete_task=true` succeeds)

Payload shape:

```json
{
  "event": "worktree.remove.after",
  "task": {"id": 7, "status": "completed"},
  "worktree": {"name": "auth-refactor", "path": "...", "status": "removed"},
  "ts": 1730000000
}
```

This gives you three things: policy decoupling (audit and notifications stay outside the core flow), failure compensation (`*.failed` records mark partial transitions), and queryability (`worktree_events` tool reads the log directly).

## What Changed From s11

| Component          | Before (s11)               | After (s12)                                  |
|--------------------|----------------------------|----------------------------------------------|
| Coordination state | Task board (`owner/status`) | Task board + explicit `worktree` binding     |
| Execution scope    | Shared directory            | Task-scoped isolated directory               |
| Recoverability     | Task status only            | Task status + worktree index                 |
| Teardown semantics | Task completion             | Task completion + explicit keep/remove       |
| Lifecycle visibility | Implicit in logs          | Explicit events in `.worktrees/events.jsonl` |

## Design Rationale

Separating control plane from execution plane means you can reason about _what to do_ and _where to do it_ independently. A task can exist without a worktree (planning phase). A worktree can exist without a task (ad-hoc exploration). Binding them is an explicit action that writes state to both sides. This composability is the point -- it keeps the system recoverable after crashes. After an interruption, state reconstructs from `.tasks/` + `.worktrees/index.json` on disk. Volatile in-memory session state downgrades into explicit, durable file state. The event stream adds observability without coupling side effects into the critical path: auditing, notifications, and quota checks consume events rather than intercepting state writes.

## Try It

```sh
cd learn-claude-code
python agents/s12_worktree_task_isolation.py
```

Example prompts to try:

1. `Create tasks for backend auth and frontend login page, then list tasks.`
2. `Create worktree "auth-refactor" for task 1, create worktree "ui-login", then bind task 2 to "ui-login".`
3. `Run "git status --short" in worktree "auth-refactor".`
4. `Keep worktree "ui-login", then list worktrees and inspect worktree events.`
5. `Remove worktree "auth-refactor" with complete_task=true, then list tasks/worktrees/events.`
