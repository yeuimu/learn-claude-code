# s07: Tasks (任务系统)

`s01 > s02 > s03 > s04 > s05 > s06 | [ s07 ] s08 > s09 > s10 > s11 > s12`

> *"State survives /compact"* -- 写进文件的状态, 压缩也杀不死。

## 问题

内存里的状态 (s03 的 TodoManager) 扛不住上下文压缩 (s06)。auto_compact 一跑, 消息被摘要替换, todo list 就没了。智能体只能从摘要文本里猜 -- 有损且容易出错。

写到磁盘就不一样了: 文件状态能扛住压缩、进程重启, 后面还能给多个智能体共享 (s09+)。

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

1. TaskManager: 每个任务一个 JSON 文件, CRUD + 依赖图。

```python
class TaskManager:
    def __init__(self, tasks_dir: Path):
        self.dir = tasks_dir
        self.dir.mkdir(exist_ok=True)
        self._next_id = self._max_id() + 1

    def create(self, subject, description=""):
        task = {"id": self._next_id, "subject": subject,
                "status": "pending", "blockedBy": [],
                "blocks": [], "owner": ""}
        self._save(task)
        self._next_id += 1
        return json.dumps(task, indent=2)
```

2. 完成任务时, 自动将其 ID 从其他任务的 `blockedBy` 中移除。

```python
def _clear_dependency(self, completed_id):
    for f in self.dir.glob("task_*.json"):
        task = json.loads(f.read_text())
        if completed_id in task.get("blockedBy", []):
            task["blockedBy"].remove(completed_id)
            self._save(task)
```

3. `update` 处理状态变更和依赖关联。

```python
def update(self, task_id, status=None,
           add_blocked_by=None, add_blocks=None):
    task = self._load(task_id)
    if status:
        task["status"] = status
        if status == "completed":
            self._clear_dependency(task_id)
    self._save(task)
```

4. 四个任务工具加入 dispatch map。

```python
TOOL_HANDLERS = {
    # ...base tools...
    "task_create": lambda **kw: TASKS.create(kw["subject"]),
    "task_update": lambda **kw: TASKS.update(kw["task_id"], kw.get("status")),
    "task_list":   lambda **kw: TASKS.list_all(),
    "task_get":    lambda **kw: TASKS.get(kw["task_id"]),
}
```

从 s07 起, Task 是多步工作的默认选择。Todo 仍可用于快速清单。

## 相对 s06 的变更

| 组件 | 之前 (s06) | 之后 (s07) |
|---|---|---|
| Tools | 5 | 8 (`task_create/update/list/get`) |
| 状态存储 | 仅内存 | `.tasks/` 中的 JSON 文件 |
| 依赖关系 | 无 | `blockedBy + blocks` 图 |
| 持久化 | 压缩后丢失 | 压缩后存活 |

## 试一试

```sh
cd learn-claude-code
python agents/s07_task_system.py
```

试试这些 prompt (英文 prompt 对 LLM 效果更好, 也可以用中文):

1. `Create 3 tasks: "Setup project", "Write code", "Write tests". Make them depend on each other in order.`
2. `List all tasks and show the dependency graph`
3. `Complete task 1 and then list tasks to see task 2 unblocked`
4. `Create a task board for refactoring: parse -> transform -> emit -> test`
