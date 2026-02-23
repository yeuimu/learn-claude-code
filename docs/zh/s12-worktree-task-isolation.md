# s12: Worktree + 任务隔离

> 目录隔离, 任务 ID 协调 -- 用"任务板 (控制面) + worktree (执行面)"把并行改动从互相污染变成可追踪、可恢复、可收尾。

## 问题

s11 时, agent 已经能认领任务并协同推进。但所有任务共享同一个工作目录。两个 agent 同时改同一棵文件树时, 未提交的变更互相干扰, 任务状态和实际改动对不上, 收尾时也无法判断该保留还是清理哪些文件。

考虑一个具体场景: agent A 在做 auth 重构, agent B 在做登录页。两者都修改了 `config.py`。A 的半成品改动被 B 的 `git status` 看到, B 以为是自己的遗留, 尝试提交 -- 结果两个任务都坏了。

根因是"做什么"和"在哪里做"没有分开。任务板管目标, 但执行上下文是共享的。解决方案: 给每个任务分配独立的 git worktree 目录, 用任务 ID 把两边关联起来。

## 解决方案

```
控制面 (.tasks/)             执行面 (.worktrees/)
+------------------+         +------------------------+
| task_1.json      |         | auth-refactor/         |
|   status: in_progress  <---->   branch: wt/auth-refactor
|   worktree: "auth-refactor" |   task_id: 1           |
+------------------+         +------------------------+
| task_2.json      |         | ui-login/              |
|   status: pending    <---->   branch: wt/ui-login
|   worktree: "ui-login"  |   task_id: 2           |
+------------------+         +------------------------+
                              |
                    index.json (worktree registry)
                    events.jsonl (lifecycle log)
```

三层状态:
1. 控制面 (What): `.tasks/task_*.json` -- 任务目标、责任归属、完成状态
2. 执行面 (Where): `.worktrees/index.json` -- 隔离目录路径、分支、存活状态
3. 运行态 (Now): 单轮内存上下文 -- 当前任务、当前 worktree、工具结果

状态机:
```text
Task:     pending -> in_progress -> completed
Worktree: absent  -> active      -> removed | kept
```

## 工作原理

1. 创建任务, 把目标写入任务板。

```python
TASKS.create("Implement auth refactor")
# -> .tasks/task_1.json  status=pending  worktree=""
```

2. 创建 worktree 并绑定任务。传入 `task_id` 时自动把任务推进到 `in_progress`。

```python
WORKTREES.create("auth-refactor", task_id=1)
# -> git worktree add -b wt/auth-refactor .worktrees/auth-refactor HEAD
# -> index.json 追加 entry, task_1.json 绑定 worktree="auth-refactor"
```

3. 在隔离目录中执行命令。`cwd` 指向 worktree 路径, 主目录不受影响。

```python
WORKTREES.run("auth-refactor", "git status --short")
# -> subprocess.run(command, cwd=".worktrees/auth-refactor", ...)
```

4. 观测和回写。`worktree_status` 查看 git 状态, `task_update` 维护进度。

```python
WORKTREES.status("auth-refactor")  # git status inside worktree
TASKS.update(1, owner="agent-A")   # update task metadata
```

5. 收尾: 选择 keep 或 remove。`remove` 配合 `complete_task=true` 会同时完成任务并解绑 worktree。

```python
WORKTREES.remove("auth-refactor", complete_task=True)
# -> git worktree remove
# -> task_1.json status=completed, worktree=""
# -> index.json  status=removed
# -> events.jsonl 写入 task.completed + worktree.remove.after
```

6. 进程中断后, 从 `.tasks/` + `.worktrees/index.json` 重建现场。会话记忆是易失的, 磁盘状态是持久的。

## 核心代码

事件流 -- append-only 生命周期日志 (来自 `agents/s12_worktree_task_isolation.py`):

```python
class EventBus:
    def emit(self, event, task=None, worktree=None, error=None):
        payload = {
            "event": event,
            "ts": time.time(),
            "task": task or {},
            "worktree": worktree or {},
        }
        if error:
            payload["error"] = error
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
```

事件流写入 `.worktrees/events.jsonl`, 每个关键操作发出三段式事件:
- `worktree.create.before / after / failed`
- `worktree.remove.before / after / failed`
- `task.completed` (当 `complete_task=true` 成功时)

事件负载形状:

```json
{
  "event": "worktree.remove.after",
  "task": {"id": 7, "status": "completed"},
  "worktree": {"name": "auth-refactor", "path": "...", "status": "removed"},
  "ts": 1730000000
}
```

任务绑定 -- Task 侧持有 worktree 名称:

```python
def bind_worktree(self, task_id: int, worktree: str, owner: str = "") -> str:
    task = self._load(task_id)
    task["worktree"] = worktree
    if task["status"] == "pending":
        task["status"] = "in_progress"
    self._save(task)
```

隔离执行 -- cwd 路由到 worktree 目录:

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

收尾联动 -- remove 同时完成任务:

```python
def remove(self, name, force=False, complete_task=False):
    self._run_git(["worktree", "remove", wt["path"]])
    if complete_task and wt.get("task_id") is not None:
        self.tasks.update(wt["task_id"], status="completed")
        self.tasks.unbind_worktree(wt["task_id"])
        self.events.emit("task.completed", ...)
```

生命周期工具注册:

```python
"worktree_keep":   lambda **kw: WORKTREES.keep(kw["name"]),
"worktree_events": lambda **kw: EVENTS.list_recent(kw.get("limit", 20)),
```

## 相对 s11 的变更

| 组件           | 之前 (s11)                 | 之后 (s12)                              |
|----------------|----------------------------|-----------------------------------------|
| 协调状态       | 任务板 (owner/status)      | 任务板 + `worktree` 显式绑定            |
| 执行上下文     | 共享目录                   | 每个任务可分配独立 worktree 目录        |
| 可恢复性       | 依赖任务状态               | 任务状态 + worktree 索引双重恢复        |
| 收尾语义       | 任务完成                   | 任务完成 + worktree 显式 keep/remove    |
| 生命周期可见性 | 隐式日志                   | `.worktrees/events.jsonl` 显式事件流    |

## 设计原理

控制面/执行面分离是这一章的核心模式。Task 管"做什么", worktree 管"在哪做", 两者通过 task ID 关联但不强耦合。这意味着一个任务可以先不绑定 worktree (纯规划阶段), 也可以在多个 worktree 之间迁移。

显式状态机让每次迁移都可审计、可恢复。进程崩溃后, 从 `.tasks/` 和 `.worktrees/index.json` 两个文件就能重建全部现场, 不依赖会话内存。

事件流是旁路可观测层, 不替代主状态机写入。审计、通知、配额控制等副作用放在事件消费者中处理, 核心流程保持最小。`keep/remove` 作为显式收尾动作存在, 而不是隐式清理 -- agent 必须做出决策, 这个决策本身被记录。

## 试一试

```sh
cd learn-claude-code
python agents/s12_worktree_task_isolation.py
```

可以尝试的提示:

1. `Create tasks for backend auth and frontend login page, then list tasks.`
2. `Create worktree "auth-refactor" for task 1, create worktree "ui-login", then bind task 2 to "ui-login".`
3. `Run "git status --short" in worktree "auth-refactor".`
4. `Keep worktree "ui-login", then list worktrees and inspect worktree events.`
5. `Remove worktree "auth-refactor" with complete_task=true, then list tasks/worktrees/events.`
