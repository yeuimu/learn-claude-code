# s01: The Agent Loop

> AIコーディングエージェントの中核は、モデルが「終了」と判断するまでツール結果をモデルにフィードバックし続ける while ループにある。

## 問題

なぜ言語モデルは単体でコーディングの質問に答えられないのか。それはコーディングが「現実世界とのインタラクション」を必要とするからだ。モデルはファイルを読み、テストを実行し、エラーを確認し、反復する必要がある。一回のプロンプト-レスポンスのやり取りではこれは実現できない。

agent loopがなければ、ユーザーが自分でモデルの出力をコピーペーストして戻す必要がある。つまりユーザー自身がループの役割を果たすことになる。agent loopはこれを自動化する: モデルを呼び出し、モデルが要求したツールを実行し、結果をフィードバックし、モデルが「完了」と言うまで繰り返す。

単純なタスクを考えてみよう: 「helloと出力するPythonファイルを作成せよ」。モデルは(1)ファイルを書くことを決定し、(2)書き、(3)動作を検証する必要がある。最低でも3回のツール呼び出しが必要だ。ループがなければ、そのたびに手動の介入が必要になる。

## 解決策

```
+----------+      +-------+      +---------+
|   User   | ---> |  LLM  | ---> |  Tool   |
|  prompt  |      |       |      | execute |
+----------+      +---+---+      +----+----+
                      ^               |
                      |   tool_result |
                      +---------------+
                      (loop continues)

The loop terminates when stop_reason != "tool_use".
That single condition is the entire control flow.
```

## 仕組み

1. ユーザーがプロンプトを入力する。これが最初のメッセージになる。

```python
history.append({"role": "user", "content": query})
```

2. メッセージ配列がツール定義と共にLLMに送信される。

```python
response = client.messages.create(
    model=MODEL, system=SYSTEM, messages=messages,
    tools=TOOLS, max_tokens=8000,
)
```

3. アシスタントのレスポンスがメッセージに追加される。

```python
messages.append({"role": "assistant", "content": response.content})
```

4. stop reasonを確認する。モデルがツールを呼び出さなかった場合、ループは終了する。この最小実装では、これが唯一のループ終了条件だ。

```python
if response.stop_reason != "tool_use":
    return
```

5. レスポンス中の各tool_useブロックについて、ツール(このセッションではbash)を実行し、結果を収集する。

```python
for block in response.content:
    if block.type == "tool_use":
        output = run_bash(block.input["command"])
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
        })
```

6. 結果がuserメッセージとして追加され、ループが続行する。

```python
messages.append({"role": "user", "content": results})
```

## 主要コード

最小限のエージェント -- パターン全体が30行未満
(`agents/s01_agent_loop.py` 66-86行目):

```python
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = run_bash(block.input["command"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})
```

## 変更点

これはセッション1 -- 出発点である。前のセッションは存在しない。

| Component     | Before     | After                          |
|---------------|------------|--------------------------------|
| Agent loop    | (none)     | `while True` + stop_reason     |
| Tools         | (none)     | `bash` (one tool)              |
| Messages      | (none)     | Accumulating list              |
| Control flow  | (none)     | `stop_reason != "tool_use"`    |

## 設計原理

このループは LLM ベースエージェントの土台だ。本番実装ではエラーハンドリング、トークン計測、ストリーミング、リトライに加え、権限ポリシーやライフサイクル編成が追加されるが、コアの相互作用パターンはここから始まる。シンプルさこそこの章の狙いであり、この最小実装では 1 つの終了条件(`stop_reason != "tool_use"`)で学習に必要な制御を示す。本コースの他の要素はこのループに積み重なる。つまり、このループの理解は基礎であって、本番アーキテクチャ全体そのものではない。

## 試してみる

```sh
cd learn-claude-code
python agents/s01_agent_loop.py
```

試せるプロンプト例:

1. `Create a file called hello.py that prints "Hello, World!"`
2. `List all Python files in this directory`
3. `What is the current git branch?`
4. `Create a directory called test_output and write 3 files in it`
