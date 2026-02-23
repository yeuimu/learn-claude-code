# s12: Worktree + Task Isolation

> ディレクトリで分離し、タスクIDで調整する -- タスクボード(制御面)と worktree(実行面)の組み合わせで、並行編集を衝突しやすい状態から追跡可能・復元可能・後片付け可能な状態に変える。

## 問題

s11 でエージェントはタスクを自律的に処理できるようになった。だが全タスクが同じ作業ディレクトリで走ると、3つの障害が現れる。

あるエージェントが認証リファクタリングに取り組みながら、別のエージェントがログインページを作っている。両者が `src/auth.py` を編集する。未コミットの変更が混ざり合い、`git diff` は2つのタスクの差分が入り混じった結果を返す。どちらのエージェントの変更かを後から特定するのは困難になり、片方のタスクを巻き戻すと他方の編集も消える。

1. 変更汚染: 未コミット変更が相互に干渉する。
2. 責務の曖昧化: タスク状態とファイル変更がずれる。
3. 終了処理の難化: 実行コンテキストを残すか削除するかの判断が曖昧になる。

解決の核は「何をやるか」と「どこでやるか」の分離だ。

## 解決策

```
Control Plane (.tasks/)          Execution Plane (.worktrees/)
+---------------------+         +------------------------+
| task_1.json         |         | auth-refactor/         |
|   status: in_progress|  bind  |   branch: wt/auth-ref  |
|   worktree: auth-ref|-------->|   cwd for commands     |
+---------------------+         +------------------------+
| task_2.json         |         | ui-login/              |
|   status: pending   |  bind   |   branch: wt/ui-login  |
|   worktree: ui-login|-------->|   cwd for commands     |
+---------------------+         +------------------------+
        |                                |
        v                                v
  "what to do"                   "where to execute"

Events (.worktrees/events.jsonl)
  worktree.create.before -> worktree.create.after
  worktree.remove.before -> worktree.remove.after
  task.completed
```

## 仕組み

1. 状態は3つの層に分かれる。制御面はタスクの目標と担当を管理し、実行面は worktree のパスとブランチを管理し、実行時状態はメモリ上の1ターン情報を保持する。

```text
制御面    (.tasks/task_*.json)        -> id/subject/status/owner/worktree
実行面    (.worktrees/index.json)     -> name/path/branch/task_id/status
実行時状態 (メモリ)                    -> current_task/current_worktree/error
```

2. Task と worktree はそれぞれ独立した状態機械を持つ。

```text
Task:     pending -> in_progress -> completed
Worktree: absent  -> active      -> removed | kept
```

3. `task_create` でまず目標を永続化する。worktree はまだ不要だ。

```python
task = {
    "id": self._next_id,
    "subject": subject,
    "status": "pending",
    "owner": "",
    "worktree": "",
    "created_at": time.time(),
    "updated_at": time.time(),
}
self._save(task)
```

4. `worktree_create(name, task_id?)` で分離ディレクトリとブランチを作る。`task_id` を渡すと、タスクが `pending` なら自動的に `in_progress` に遷移する。

```python
entry = {
    "name": name,
    "path": str(path),
    "branch": branch,
    "task_id": task_id,
    "status": "active",
    "created_at": time.time(),
}
idx["worktrees"].append(entry)
self._save_index(idx)

if task_id is not None:
    self.tasks.bind_worktree(task_id, name)
```

5. `worktree_run(name, command)` で分離ディレクトリ内のコマンドを実行する。`cwd=worktree_path` が実質的な「enter」だ。

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

6. 終了処理では `keep` か `remove` を明示的に選ぶ。`worktree_remove(name, complete_task=true)` はディレクトリ削除とタスク完了を一度に行う。

```python
def remove(self, name: str, force: bool = False, complete_task: bool = False) -> str:
    self._run_git(["worktree", "remove", wt["path"]])
    if complete_task and wt.get("task_id") is not None:
        self.tasks.update(wt["task_id"], status="completed")
        self.tasks.unbind_worktree(wt["task_id"])
        self.events.emit("task.completed", ...)
```

7. `.worktrees/events.jsonl` にライフサイクルイベントが append-only で記録される。重要な遷移には `before / after / failed` の三段イベントが出力される。

```json
{
  "event": "worktree.remove.after",
  "task": {"id": 7, "status": "completed"},
  "worktree": {"name": "auth-refactor", "path": "...", "status": "removed"},
  "ts": 1730000000
}
```

イベントは可観測性のサイドチャネルであり、task/worktree の主状態機械の書き込みを置き換えるものではない。監査・通知・ポリシーチェックはイベント購読側で処理する。

## 主要コード

タスクの worktree バインドと状態遷移(`agents/s12_worktree_task_isolation.py` 182-191行目):

```python
def bind_worktree(self, task_id: int, worktree: str, owner: str = "") -> str:
    task = self._load(task_id)
    task["worktree"] = worktree
    if owner:
        task["owner"] = owner
    if task["status"] == "pending":
        task["status"] = "in_progress"
    task["updated_at"] = time.time()
    self._save(task)
    return json.dumps(task, indent=2)
```

Worktree の作成とイベント発火(`agents/s12_worktree_task_isolation.py` 283-334行目):

```python
def create(self, name: str, task_id: int = None, base_ref: str = "HEAD") -> str:
    self._validate_name(name)
    if self._find(name):
        raise ValueError(f"Worktree '{name}' already exists in index")

    path = self.dir / name
    branch = f"wt/{name}"
    self.events.emit("worktree.create.before",
        task={"id": task_id} if task_id is not None else {},
        worktree={"name": name, "base_ref": base_ref})
    try:
        self._run_git(["worktree", "add", "-b", branch, str(path), base_ref])
        entry = {
            "name": name, "path": str(path), "branch": branch,
            "task_id": task_id, "status": "active",
            "created_at": time.time(),
        }
        idx = self._load_index()
        idx["worktrees"].append(entry)
        self._save_index(idx)
        if task_id is not None:
            self.tasks.bind_worktree(task_id, name)
        self.events.emit("worktree.create.after", ...)
        return json.dumps(entry, indent=2)
    except Exception as e:
        self.events.emit("worktree.create.failed", ..., error=str(e))
        raise
```

ツールディスパッチマップ(`agents/s12_worktree_task_isolation.py` 535-552行目):

```python
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "task_create": lambda **kw: TASKS.create(kw["subject"], kw.get("description", "")),
    "task_list": lambda **kw: TASKS.list_all(),
    "task_get": lambda **kw: TASKS.get(kw["task_id"]),
    "task_update": lambda **kw: TASKS.update(kw["task_id"], kw.get("status"), kw.get("owner")),
    "task_bind_worktree": lambda **kw: TASKS.bind_worktree(kw["task_id"], kw["worktree"], kw.get("owner", "")),
    "worktree_create": lambda **kw: WORKTREES.create(kw["name"], kw.get("task_id"), kw.get("base_ref", "HEAD")),
    "worktree_list": lambda **kw: WORKTREES.list_all(),
    "worktree_status": lambda **kw: WORKTREES.status(kw["name"]),
    "worktree_run": lambda **kw: WORKTREES.run(kw["name"], kw["command"]),
    "worktree_keep": lambda **kw: WORKTREES.keep(kw["name"]),
    "worktree_remove": lambda **kw: WORKTREES.remove(kw["name"], kw.get("force", False), kw.get("complete_task", False)),
    "worktree_events": lambda **kw: EVENTS.list_recent(kw.get("limit", 20)),
}
```

## s11 からの変更

| 観点 | s11 | s12 |
|---|---|---|
| 調整状態 | Task board (`owner/status`) | Task board + `worktree` 明示バインド |
| 実行スコープ | 共有ディレクトリ | タスク単位の分離ディレクトリ |
| 復元性 | タスク状態のみ | タスク状態 + worktree index |
| 終了意味論 | タスク完了のみ | タスク完了 + 明示的 keep/remove 判断 |
| ライフサイクル可視性 | 暗黙的なログ | `.worktrees/events.jsonl` の明示イベント |

## 設計原理

制御面と実行面の分離が中核だ。タスクは「何をやるか」を記述し、worktree は「どこでやるか」を提供する。両者は組み合わせ可能だが、強結合ではない。状態遷移は暗黙の自動掃除ではなく、`worktree_keep` / `worktree_remove` という明示的なツール操作として表現する。イベントストリームは `before / after / failed` の三段構造で重要な遷移を記録し、監査や通知をコアロジックから分離する。中断後でも `.tasks/` + `.worktrees/index.json` から状態を再構築できる。揮発的な会話状態を明示的なディスク状態に落とすことが、復元可能性の鍵だ。

## 試してみる

```sh
cd learn-claude-code
python agents/s12_worktree_task_isolation.py
```

試せるプロンプト例:

1. `Create tasks for backend auth and frontend login page, then list tasks.`
2. `Create worktree "auth-refactor" for task 1, create worktree "ui-login", then bind task 2 to "ui-login".`
3. `Run "git status --short" in worktree "auth-refactor".`
4. `Keep worktree "ui-login", then list worktrees and inspect worktree events.`
5. `Remove worktree "auth-refactor" with complete_task=true, then list tasks/worktrees/events.`
