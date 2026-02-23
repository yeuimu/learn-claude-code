# s02: Tools

> A dispatch map routes tool calls to handler functions. The loop stays identical.

## The Problem

With only `bash`, the agent shells out for everything: reading files,
writing files, editing files. This works but is fragile. `cat` output
gets truncated unpredictably. `sed` replacements fail on special
characters. The model wastes tokens constructing shell pipelines when
a direct function call would be simpler.

More importantly, bash is a security surface. Every bash call can do
anything the shell can do. With dedicated tools like `read_file` and
`write_file`, you can enforce path sandboxing and block dangerous
patterns at the tool level rather than hoping the model avoids them.

The insight is that adding tools does not require changing the loop.
The loop from s01 stays identical. You add entries to the tools array,
add handler functions, and wire them together with a dispatch map.

## The Solution

```
+----------+      +-------+      +------------------+
|   User   | ---> |  LLM  | ---> | Tool Dispatch    |
|  prompt  |      |       |      | {                |
+----------+      +---+---+      |   bash: run_bash |
                      ^          |   read: run_read |
                      |          |   write: run_wr  |
                      +----------+   edit: run_edit |
                      tool_result| }                |
                                 +------------------+

The dispatch map is a dict: {tool_name: handler_function}
One lookup replaces any if/elif chain.
```

## How It Works

1. Define handler functions for each tool. Each takes keyword arguments
   matching the tool's input_schema and returns a string result.

```python
def run_read(path: str, limit: int = None) -> str:
    text = safe_path(path).read_text()
    lines = text.splitlines()
    if limit and limit < len(lines):
        lines = lines[:limit]
    return "\n".join(lines)[:50000]
```

2. Create the dispatch map linking tool names to handlers.

```python
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"],
                                        kw["new_text"]),
}
```

3. In the agent loop, look up the handler by name instead of hardcoding.

```python
for block in response.content:
    if block.type == "tool_use":
        handler = TOOL_HANDLERS.get(block.name)
        output = handler(**block.input)
        results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": output,
        })
```

4. Path sandboxing prevents the model from escaping the workspace.

```python
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path
```

## Key Code

The dispatch pattern (from `agents/s02_tool_use.py`, lines 93-129):

```python
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw["command"]),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"],
                                        kw["new_text"]),
}

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
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input) if handler \
                    else f"Unknown tool: {block.name}"
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})
```

## What Changed From s01

| Component      | Before (s01)       | After (s02)                |
|----------------|--------------------|----------------------------|
| Tools          | 1 (bash only)      | 4 (bash, read, write, edit)|
| Dispatch       | Hardcoded bash call | `TOOL_HANDLERS` dict       |
| Path safety    | None               | `safe_path()` sandbox      |
| Agent loop     | Unchanged          | Unchanged                  |

## Design Rationale

The dispatch map scales linearly: add a tool, add a handler, add a schema entry. The loop never changes. Handlers are pure functions, so they test in isolation. Any agent that outgrows a dispatch map has a design problem, not a scaling problem.

## Try It

```sh
cd learn-claude-code
python agents/s02_tool_use.py
```

Example prompts to try:

1. `Read the file requirements.txt`
2. `Create a file called greet.py with a greet(name) function`
3. `Edit greet.py to add a docstring to the function`
4. `Read greet.py to verify the edit worked`
5. `Run the greet function with bash: python -c "from greet import greet; greet('World')"`
