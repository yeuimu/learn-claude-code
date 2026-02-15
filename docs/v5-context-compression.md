# v5: Context Compression

**Core insight: Forgetting is a feature, not a bug.**

v0-v4 share an implicit assumption: conversation history can grow forever. In practice, it can't.

## The Problem

```sh
200K token context window:
  [System prompt]       ~2K tokens
  [CLAUDE.md]           ~3K tokens
  [Tool definitions]    ~8K tokens
  [Conversation]        keeps growing...
  [Tool call #50]       -> approaching 180K tokens
  [Tool call #60]       -> exceeds 200K, request fails
```

A complex refactoring task can take 100+ tool calls. Without compression, the agent hits the wall.

## Token Budget Waterfall

```sh
Context Window: 200,000 tokens
+------------------------------------------------------------------+
|                                                                  |
|  [1] Output Reserve: min(max_output, 20000) = 16,384            |
|      Tokens reserved for model's response                       |
|                                                                  |
|  [2] Safety Buffer: 13,000                                      |
|      System prompt + tool defs + overhead                        |
|                                                                  |
|  [3] Available for Conversation: 170,616                         |
|      = 200,000 - 16,384 - 13,000                                |
|                                                                  |
+------------------------------------------------------------------+
         |
         v
  When conversation tokens > 170,616 AND savings >= 20000:
         |
         v
  Trigger auto_compact()
```

## Three-Layer Compression Pipeline

```sh
Every agent turn:
+------------------+
| Tool call result |
+------------------+
        |
        v
[Layer 1: Microcompact]         (silent, every turn)
  Keep last 3 tool results.
  Replace older results with:
  "[Output compacted - re-read if needed]"
        |
        v
[Check: tokens > 170616?]
        |
   no --+-- yes
   |         |
   v         v
continue  [Check: savings >= 20000?]
               |
          no --+-- yes
          |         |
          v         v
       continue  [Layer 2: Auto-compact]       (near limit)
                   1. Save transcript to disk
                   2. Restore recent files
                   3. Summarize full conversation
                   4. Keep last 5 messages
                        |
                        v
                 [Layer 3: Manual /compact]     (user-initiated)
                   Same mechanism, custom prompt

Throughout: full transcript saved to disk (JSONL).
```

| Layer | Trigger | Action | User Awareness |
|-------|---------|--------|---------------|
| Microcompact | Every turn (auto) | Replace old tool outputs | Invisible |
| Auto-compact | Near context limit | Summarize entire conversation | User sees notice |
| Manual compact | `/compact` command | Custom compression per user | User-initiated |

## Dynamic Threshold

The auto-compact threshold is not a fixed constant. It is calculated from the model's actual limits:

```python
def auto_compact_threshold(context_window=200000, max_output=16384):
    """threshold = context_window - min(max_output, 20000) - 13000"""
    output_reserve = min(max_output, 20000)
    return context_window - output_reserve - 13000
    # For 200K window: 200000 - 16384 - 13000 = 170616
```

The 13000 buffer accounts for system prompt, tool definitions, and overhead. The `min(max_output, 20000)` cap prevents models with very large max_output from triggering compression too early.

## Min-Savings Guard

Compaction is skipped if the estimated savings are too small:

```python
MIN_SAVINGS = 20000

def should_compact(messages):
    total = sum(estimate_tokens(m) for m in messages)
    if total <= TOKEN_THRESHOLD:
        return False
    recent_size = sum(estimate_tokens(m) for m in messages[-5:])
    savings = total - recent_size
    return savings >= MIN_SAVINGS
```

Without this guard, a long conversation with most tokens in the last 5 messages would trigger compression that achieves nothing.

Production value: MIN_SAVINGS = 20000 (matches cli.js zUY=20000).
For demos: try MIN_SAVINGS=2000 to observe compaction in short sessions.

## Microcompact: Silent Cleanup

After each turn, replace old large tool outputs with placeholders, keeping only recent ones:

```python
COMPACTABLE_TOOLS = {"bash", "read_file", "write_file", "edit_file"}
KEEP_RECENT = 3

def microcompact(messages):
    """Replace old large tool results with placeholders."""
    tool_results = find_tool_results(messages, COMPACTABLE_TOOLS)

    for result in tool_results[:-KEEP_RECENT]:
        if estimate_tokens(result) > 1000:
            result["content"] = "[Output compacted - re-read if needed]"

    return messages
```

Key: only the **content** is cleared. The tool call structure stays intact. The model still knows what it called, just can't see old output. Re-read if needed.

## Token Estimation

Tokens are estimated using the character-based formula from cli.js:

```python
@staticmethod
def estimate_tokens(text: str) -> int:
    # cli.js H2: Math.round(A.length / q) with default divisor q=4
    return len(text) // 4
```

This approximates 4 characters per token, matching the cli.js ground truth formula.

## Auto-Compact Threshold

The threshold is calculated dynamically, not a fixed percentage:

```python
def auto_compact_threshold(context_window=200000, max_output=16384):
    output_reserve = min(max_output, 20000)
    return context_window - output_reserve - 13000
    # For 200K window: 200000 - 16384 - 13000 = 170616 (85.3%)
```

Note: Claude Code detects external file changes via mtime comparison at each turn
boundary -- not via real-time file watchers. This means changes are noticed at
the start of each new model turn, not mid-response.

The production system computes 28 different attachment types before each model turn
(changed files, todo reminders, team context, etc.), all wrapped in `<system-reminder>` tags.
Our simplified version teaches the core pattern with microcompact and auto-compact.

## Auto-Compact: Full Summary

Triggered when context exceeds the dynamic threshold and savings justify it:

```python
def auto_compact(messages):
    # 1. Save full transcript to disk (never lost)
    save_transcript(messages)

    # 2. Capture recently-read files before compaction
    restored_files = restore_recent_files(messages)

    # 3. Use model to generate summary
    summary = call_api("Summarize this conversation chronologically: "
                       "goals, actions, decisions, current state...")

    # 4. Replace old messages with summary, keep recent turns
    compressed = [
        {"role": "user", "content": f"[Conversation compressed]\n{summary}"},
        {"role": "assistant", "content": "Understood. Continuing with compressed context."},
    ]
    # Interleave restored files as user/assistant pairs
    for rf in restored_files:
        compressed.append(rf)
        compressed.append({"role": "assistant", "content": "Noted, file content restored."})
    compressed.extend(messages[-5:])

    return compressed
```

**Key design**: the summary is injected into conversation history (user message), not into the system prompt. This keeps the system prompt's cache intact.

## Post-Compact File Restoration

After compression, recently-read files are restored into context so the agent does not have to re-read them:

```python
MAX_RESTORE_FILES = 5
MAX_RESTORE_TOKENS_PER_FILE = 5000
MAX_RESTORE_TOKENS_TOTAL = 50000

def restore_recent_files(messages):
    """Scan messages for read_file calls, restore recent ones."""
    # Walk messages backward, collect unique file paths
    # Read each file, truncate to MAX_RESTORE_TOKENS_PER_FILE
    # Stop when MAX_RESTORE_FILES or MAX_RESTORE_TOKENS_TOTAL reached
```

This ensures the agent retains awareness of files it was recently working on, without needing to re-read them after compression.

## Large Output Demotion

When a single tool output is too large, save to disk and return a preview:

```python
MAX_OUTPUT_TOKENS = 40000

def handle_large_output(output):
    if estimate_tokens(output) > MAX_OUTPUT_TOKENS:
        path = save_to_disk(output)
        return f"Output too large. Saved to: {path}\nPreview:\n{output[:2000]}..."
    return output
```

## Subagents Compress Too

v3 subagents have their own context windows, and run compression independently:

```python
def run_subagent(prompt, agent_type):
    sub_messages = [{"role": "user", "content": prompt}]

    while True:
        sub_messages = microcompact(sub_messages)
        if should_compact(sub_messages):
            sub_messages = auto_compact(sub_messages)

        response = call_api(sub_messages)
        if response.stop_reason != "tool_use":
            break
        # ...

    return extract_final_text(response)
```

Disk persistence from compression lays the groundwork for later mechanisms: the Tasks system (v6) and multi-agent collaboration (v8) store data on disk, unaffected by compression.

## The Deeper Insight

> **Human working memory is limited too.**

We don't remember every line of code we wrote. We remember "what was done, why, and current state." Compression mirrors this cognitive pattern:

- Microcompact = short-term memory decay
- Auto-compact = shifting from detail memory to concept memory
- Disk transcript = retrievable long-term memory

The full record is always on disk. Compression only affects working memory, not the archive.

---

**Context is finite, work is infinite. Compression keeps the agent going.**

[<-- v4](./v4-skills-mechanism.md) | [Back to README](../README.md) | [v6 -->](./v6-tasks-system.md)
