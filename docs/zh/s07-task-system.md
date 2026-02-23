# s07: Tasks (任务系统)

> 任务以 JSON 文件形式持久化在文件系统上, 带有依赖图, 因此它们能在上下文压缩后存活, 也可以跨智能体共享。

## 问题

内存中的状态 (如 s03 的 TodoManager) 在上下文压缩 (s06) 时会丢失。auto_compact 用摘要替换消息后, 待办列表就没了。智能体只能从摘要文本中重建它, 这是有损且容易出错的。

这就是 s06 到 s07 的关键桥梁: TodoManager 的条目随压缩消亡; 基于文件的任务不会。将状态移到文件系统上使其不受压缩影响。

更根本地说, 内存中的状态对其他智能体不可见。当我们最终构建团队 (s09+) 时, 队友需要一个共享的任务看板。内存中的数据结构是进程局部的。

解决方案是将任务作为 JSON 文件持久化在 `.tasks/` 目录中。每个任务是一个单独的文件, 包含 ID、主题、状态和依赖图。完成任务 1 会自动解除任务 2 的阻塞 (如果任务 2 有 `blockedBy: [1]`)。在本教学实现里, 文件系统是任务状态的真实来源。

## Task vs Todo: 何时用哪个

从 s07 起, Task 是默认主线。Todo 仍可用于短期线性清单。

## 快速判定矩阵

| 场景 | 优先选择 | 原因 |
|---|---|---|
| 短时、单会话、线性清单 | Todo | 心智负担最低，记录最快 |
| 跨会话、存在依赖、多人协作 | Task | 状态可持久、依赖可表达、协作可见 |
| 一时拿不准 | Task | 后续降级更容易，半途迁移成本更低 |

## 解决方案

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

## 工作原理

1. TaskManager 提供 CRUD 操作。每个任务是一个 JSON 文件。

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

2. 当任务标记为 completed 时, `_clear_dependency` 将其 ID 从所有其他任务的 `blockedBy` 列表中移除。

```python
def _clear_dependency(self, completed_id: int):
    for f in self.dir.glob("task_*.json"):
        task = json.loads(f.read_text())
        if completed_id in task.get("blockedBy", []):
            task["blockedBy"].remove(completed_id)
            self._save(task)
```

3. `update` 方法处理状态变更和双向依赖关联。

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

4. 四个任务工具添加到 dispatch map。

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

## 核心代码

带依赖图的 TaskManager (来自 `agents/s07_task_system.py`, 第 46-123 行):

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

## 相对 s06 的变更

| 组件 | 之前 (s06) | 之后 (s07) |
|---|---|---|
| Tools | 5 | 8 (`task_create/update/list/get`) |
| 状态存储 | 仅内存 | `.tasks/` 中的 JSON 文件 |
| 依赖关系 | 无 | `blockedBy + blocks` 图 |
| 持久化 | 压缩后丢失 | 压缩后存活 |

## 设计原理

基于文件的状态能在上下文压缩中存活。当智能体的对话被压缩时, 内存中的状态会丢失, 但写入磁盘的任务会持久保存。依赖图确保即使在上下文丢失后也能按正确顺序执行。这是临时对话与持久工作之间的桥梁 -- 智能体可以忘记对话细节, 但始终有任务看板来提醒它还需要做什么。在本教学实现里, 文件系统作为任务状态真实来源也为未来的多智能体共享提供了基础, 因为任何进程都可以读取相同的 JSON 文件。

但“持久化”成立有前提：每次写入前都要重新读取任务文件，确认 `status/blockedBy` 与预期一致，再原子写回。否则并发写入很容易互相覆盖状态。

从课程设计上看, 这也是为什么 s07 之后我们默认采用 Task 而不是 Todo: 它更接近真实工程中的长期执行与协作需求。

## 试一试

```sh
cd learn-claude-code
python agents/s07_task_system.py
```

可以尝试的提示:

1. `Create 3 tasks: "Setup project", "Write code", "Write tests". Make them depend on each other in order.`
2. `List all tasks and show the dependency graph`
3. `Complete task 1 and then list tasks to see task 2 unblocked`
4. `Create a task board for refactoring: parse -> transform -> emit -> test`
