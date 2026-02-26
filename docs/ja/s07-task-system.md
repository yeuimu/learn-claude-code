# s07: Tasks

`s01 > s02 > s03 > s04 > s05 > s06 | [ s07 ] s08 > s09 > s10 > s11 > s12`

> *"State survives /compact"* -- ファイルベースの状態はコンテキスト圧縮を生き延びる。

## 問題

インメモリ状態(s03のTodoManager)はコンテキスト圧縮(s06)で消える。auto_compactがメッセージを要約に置換した後、todoリストは失われる。要約テキストからの復元は不正確で脆い。

ファイルベースのタスクがこれを解決する: 状態をディスクに書き込めば、圧縮もプロセス再起動も生き延び、やがてマルチエージェントでの共有(s09+)も可能になる。

## 解決策

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

## 仕組み

1. TaskManager: タスクごとに1つのJSONファイル、依存グラフ付きCRUD。

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

2. タスク完了時に、他タスクの`blockedBy`リストから完了IDを除去する。

```python
def _clear_dependency(self, completed_id):
    for f in self.dir.glob("task_*.json"):
        task = json.loads(f.read_text())
        if completed_id in task.get("blockedBy", []):
            task["blockedBy"].remove(completed_id)
            self._save(task)
```

3. `update`が状態遷移と依存配線を担う。

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

4. 4つのタスクツールをディスパッチマップに追加する。

```python
TOOL_HANDLERS = {
    # ...base tools...
    "task_create": lambda **kw: TASKS.create(kw["subject"]),
    "task_update": lambda **kw: TASKS.update(kw["task_id"], kw.get("status")),
    "task_list":   lambda **kw: TASKS.list_all(),
    "task_get":    lambda **kw: TASKS.get(kw["task_id"]),
}
```

s07以降、Taskがマルチステップ作業のデフォルト。Todoは軽量チェックリスト用に残る。

## s06からの変更点

| Component | Before (s06) | After (s07) |
|---|---|---|
| Tools | 5 | 8 (`task_create/update/list/get`) |
| State storage | In-memory only | JSON files in `.tasks/` |
| Dependencies | None | `blockedBy + blocks` graph |
| Persistence | Lost on compact | Survives compression |

## 試してみる

```sh
cd learn-claude-code
python agents/s07_task_system.py
```

1. `Create 3 tasks: "Setup project", "Write code", "Write tests". Make them depend on each other in order.`
2. `List all tasks and show the dependency graph`
3. `Complete task 1 and then list tasks to see task 2 unblocked`
4. `Create a task board for refactoring: parse -> transform -> emit -> test`
