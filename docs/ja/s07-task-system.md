# s07: Tasks

> タスクを依存グラフ付き JSON として永続化し、コンテキスト圧縮後も状態を保持し、複数エージェントで共有できるようにする。

## 問題

インメモリ状態（s03 の TodoManager など）は、s06 の圧縮後に失われやすい。古いターンが要約化されると、Todo 状態は会話の外に残らない。

s06 -> s07 の本質は次の切替:

1. メモリ上 Todo は会話依存で失われやすい。
2. ディスク上 Task は永続で復元しやすい。

さらに可視性の問題がある。インメモリ構造はプロセスローカルであり、チームメイト間の共有が不安定になる。

## Task vs Todo: 使い分け

s07 以降は Task がデフォルト。Todo は短い直線的チェックリスト用に残る。

## クイック判定マトリクス

| 状況 | 優先 | 理由 |
|---|---|---|
| 短時間・単一セッション・直線的チェック | Todo | 儀式が最小で記録が速い |
| セッション跨ぎ・依存関係・複数担当 | Task | 永続性、依存表現、協調可視性が必要 |
| 迷う場合 | Task | 後で簡略化する方が、途中移行より低コスト |

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

1. TaskManager はタスクごとに1 JSON ファイルで CRUD を提供する。

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

2. タスク完了時、他タスクの依存を解除する。

```python
def _clear_dependency(self, completed_id: int):
    for f in self.dir.glob("task_*.json"):
        task = json.loads(f.read_text())
        if completed_id in task.get("blockedBy", []):
            task["blockedBy"].remove(completed_id)
            self._save(task)
```

3. `update` が状態遷移と依存配線を担う。

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

4. タスクツール群をディスパッチへ追加する。

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

## 主要コード

依存グラフ付き TaskManager（`agents/s07_task_system.py` 46-123行）:

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

## s06 からの変更

| 項目 | Before (s06) | After (s07) |
|---|---|---|
| Tools | 5 | 8 (`task_create/update/list/get`) |
| 状態保存 | メモリのみ | `.tasks/` の JSON |
| 依存関係 | なし | `blockedBy + blocks` グラフ |
| 永続性 | compact で消失 | compact 後も維持 |

## 設計原理

ファイルベース状態は compaction や再起動に強い。依存グラフにより、会話詳細を忘れても実行順序を保てる。これにより、会話中心の状態を作業中心の永続状態へ移せる。

ただし耐久性には運用前提がある。書き込みのたびに task JSON を再読込し、`status/blockedBy` が期待通りか確認してから原子的に保存しないと、並行更新で状態を上書きしやすい。

コース設計上、s07 以降で Task を主線に置くのは、長時間・協調開発の実態に近いから。

## 試してみる

```sh
cd learn-claude-code
python agents/s07_task_system.py
```

例:

1. `Create 3 tasks: "Setup project", "Write code", "Write tests". Make them depend on each other in order.`
2. `List all tasks and show the dependency graph`
3. `Complete task 1 and then list tasks to see task 2 unblocked`
4. `Create a task board for refactoring: parse -> transform -> emit -> test`
