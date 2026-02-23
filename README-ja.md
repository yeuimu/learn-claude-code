# Learn Claude Code -- 0 から 1 へ構築する nano Claude Code-like agent

[English](./README.md) | [中文](./README-zh.md) | [日本語](./README-ja.md)

```
                    THE AGENT PATTERN
                    =================

    User --> messages[] --> LLM --> response
                                      |
                            stop_reason == "tool_use"?
                           /                          \
                         yes                           no
                          |                             |
                    execute tools                    return text
                    append results
                    loop back -----------------> messages[]


    これは最小ループだ。すべての AI コーディングエージェントに必要な土台になる。
    本番のエージェントには、ポリシー・権限・ライフサイクル層が追加される。
```

**12 の段階的セッション、シンプルなループから分離された自律実行まで。**
**各セッションは1つのメカニズムを追加する。各メカニズムには1つのモットーがある。**

> **s01** &nbsp; *"Bash があれば十分"* &mdash; 1つのツール + 1つのループ = エージェント
>
> **s02** &nbsp; *"ループは変わらない"* &mdash; ツール追加はハンドラー追加であり、ループの作り直しではない
>
> **s03** &nbsp; *"行動する前に計画せよ"* &mdash; 可視化された計画がタスク完了率を向上させる
>
> **s04** &nbsp; *"プロセス分離 = コンテキスト分離"* &mdash; サブエージェントごとに新しい messages[]
>
> **s05** &nbsp; *"必要な時にロード、事前にではなく"* &mdash; system prompt ではなく tool_result で知識を注入
>
> **s06** &nbsp; *"戦略的忘却"* &mdash; 古いコンテキストを忘れて無限セッションを実現
>
> **s07** &nbsp; *"状態は圧縮を生き延びる"* &mdash; ファイルベースの状態はコンテキスト圧縮に耐える
>
> **s08** &nbsp; *"撃ちっ放し"* &mdash; ノンブロッキングスレッド + 通知キュー
>
> **s09** &nbsp; *"追記で送信、排出で読取"* &mdash; 永続チームメイトのための非同期メールボックス
>
> **s10** &nbsp; *"同じ request_id、2つのプロトコル"* &mdash; 1つの FSM パターンでシャットダウン + プラン承認
>
> **s11** &nbsp; *"ポーリング、クレーム、作業、繰り返し"* &mdash; コーディネーター不要、エージェントが自己組織化
>
> **s12** &nbsp; *"ディレクトリで分離し、タスクIDで調整する"* &mdash; タスクボード + 必要時の worktree レーン

---

## コアパターン

```python
def agent_loop(messages):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM,
            messages=messages, tools=TOOLS,
        )
        messages.append({"role": "assistant",
                         "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = TOOL_HANDLERS[block.name](**block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})
```

各セッションはこのループの上に1つのメカニズムを重ねる -- ループ自体は変わらない。

## スコープ (重要)

このリポジトリは、nano Claude Code-like agent を 0->1 で構築・学習するための教材プロジェクトです。
学習を優先するため、以下の本番メカニズムは意図的に簡略化または省略しています。

- 完全なイベント / Hook バス (例: PreToolUse, SessionStart/End, ConfigChange)。  
  s12 では教材用に最小の追記型ライフサイクルイベントのみ実装している。
- ルールベースの権限ガバナンスと信頼フロー
- セッションライフサイクル制御 (resume/fork) と高度な worktree ライフサイクル制御
- MCP ランタイムの詳細 (transport/OAuth/リソース購読/ポーリング)

このリポジトリの JSONL メールボックス方式は教材用の実装であり、特定の本番内部実装を主張するものではありません。

## クイックスタート

```sh
git clone https://github.com/shareAI-lab/learn-claude-code
cd learn-claude-code
pip install -r requirements.txt
cp .env.example .env   # .env を編集して ANTHROPIC_API_KEY を入力

python agents/s01_agent_loop.py       # ここから開始
python agents/s11_autonomous_agents.py  # 完全自律チーム
python agents/s12_worktree_task_isolation.py  # Task 対応の worktree 分離
```

### Web プラットフォーム

インタラクティブな可視化、ステップスルーアニメーション、ソースビューア、各セッションのドキュメント。

```sh
cd web && npm install && npm run dev   # http://localhost:3000
```

## 学習パス

```
フェーズ1: ループ                     フェーズ2: 計画と知識
==================                   ==============================
s01  エージェントループ      [1]     s03  TodoWrite               [5]
     while + stop_reason                  TodoManager + nag リマインダー
     |                                    |
     +-> s02  ツール             [4]     s04  サブエージェント      [5]
              dispatch map: name->handler     子ごとに新しい messages[]
                                              |
                                         s05  Skills               [5]
                                              SKILL.md を tool_result で注入
                                              |
                                         s06  Compact              [5]
                                              3層コンテキスト圧縮

フェーズ3: 永続化                     フェーズ4: チーム
==================                   =====================
s07  タスクシステム           [8]     s09  エージェントチーム      [9]
     ファイルベース CRUD + 依存グラフ      チームメイト + JSONL メールボックス
     |                                    |
s08  バックグラウンドタスク   [6]     s10  チームプロトコル        [12]
     デーモンスレッド + 通知キュー         シャットダウン + プラン承認 FSM
                                          |
                                     s11  自律エージェント        [14]
                                          アイドルサイクル + 自動クレーム
                                     |
                                     s12  Worktree 分離           [16]
                                          タスク調整 + 必要時の分離実行レーン

                                     [N] = ツール数
```

## プロジェクト構成

```
learn-claude-code/
|
|-- agents/                        # Python リファレンス実装 (s01-s12 + 完全版)
|-- docs/{en,zh,ja}/               # メンタルモデル優先のドキュメント (3言語)
|-- web/                           # インタラクティブ学習プラットフォーム (Next.js)
|-- skills/                        # s05 の Skill ファイル
+-- .github/workflows/ci.yml      # CI: 型チェック + ビルド
```

## ドキュメント

メンタルモデル優先: 問題、解決策、ASCII図、最小限のコード。
[English](./docs/en/) | [中文](./docs/zh/) | [日本語](./docs/ja/)

| セッション | トピック | モットー |
|-----------|---------|---------|
| [s01](./docs/ja/s01-the-agent-loop.md) | エージェントループ | *Bash があれば十分* |
| [s02](./docs/ja/s02-tool-use.md) | ツール | *ループは変わらない* |
| [s03](./docs/ja/s03-todo-write.md) | TodoWrite | *行動する前に計画せよ* |
| [s04](./docs/ja/s04-subagent.md) | サブエージェント | *プロセス分離 = コンテキスト分離* |
| [s05](./docs/ja/s05-skill-loading.md) | Skills | *必要な時にロード、事前にではなく* |
| [s06](./docs/ja/s06-context-compact.md) | Compact | *戦略的忘却* |
| [s07](./docs/ja/s07-task-system.md) | タスクシステム | *状態は圧縮を生き延びる* |
| [s08](./docs/ja/s08-background-tasks.md) | バックグラウンドタスク | *撃ちっ放し* |
| [s09](./docs/ja/s09-agent-teams.md) | エージェントチーム | *追記で送信、排出で読取* |
| [s10](./docs/ja/s10-team-protocols.md) | チームプロトコル | *同じ request_id、2つのプロトコル* |
| [s11](./docs/ja/s11-autonomous-agents.md) | 自律エージェント | *ポーリング、クレーム、作業、繰り返し* |
| [s12](./docs/ja/s12-worktree-task-isolation.md) | Worktree + タスク分離 | *ディレクトリで分離し、タスクIDで調整する* |

## ライセンス

MIT

---

**モデルがエージェントだ。私たちの仕事はツールを与えて邪魔しないこと。**
