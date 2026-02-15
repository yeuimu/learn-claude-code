# v4: Skills Mechanism

**Core insight: Tools are what the model CAN do. Skills are what it KNOWS how to do.**

v3 gave us subagents for task decomposition. But there is a deeper question: how does the model know HOW to handle domain-specific tasks? Processing PDFs, building MCP servers, reviewing code -- this is expertise, not capability. Skills solve this by letting the model load domain knowledge on-demand.

## Skill Loading Flow

```sh
Startup: scan skills/ directory
    |
    v
+------------------+     Layer 1: Metadata only (~100 tokens/skill)
| SkillLoader      |     name + description loaded into system prompt
| .skills = {      |
|   "pdf": {...},  |
|   "mcp": {...},  |
| }                |
+--------+---------+
         |
         |  User: "Convert this PDF to text"
         |  Model: Skill(skill="pdf")
         v
+------------------+     Layer 2: Full SKILL.md body (~2000 tokens)
| get_skill_content|     Injected as tool_result (NOT system prompt)
| -> body + hints  |     Wrapped in <skill-loaded> tags
+--------+---------+
         |
         |  Model reads instructions, finds resources
         v
+------------------+     Layer 3: On-disk resources (unlimited)
| skills/pdf/      |     scripts/, references/, assets/
|   SKILL.md       |     Model can read_file or bash to access
|   scripts/       |
|   references/    |
+------------------+
```

## SKILL.md Standard

Each skill is a folder containing a `SKILL.md` file with YAML frontmatter and Markdown body:

```sh
skills/
  pdf/
    SKILL.md              # Required: YAML frontmatter + Markdown body
  mcp-builder/
    SKILL.md
    references/           # Optional: docs, specs
  code-review/
    SKILL.md
    scripts/              # Optional: helper scripts
```

SKILL.md format:

```md
---
name: pdf
description: Process PDF files. Use when reading, creating, or merging PDFs.
---

# PDF Processing Skill

## Reading PDFs

Use pdftotext for quick extraction:
```

The YAML frontmatter provides metadata (name, description). The Markdown body provides detailed instructions.

## SkillLoader

```python
class SkillLoader:
    def __init__(self, skills_dir):
        self.skills = {}
        self.load_skills()           # Scan and parse all SKILL.md files

    def parse_skill_md(self, path):
        content = path.read_text()
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            return None
        frontmatter, body = match.groups()
        # Parse simple YAML key: value pairs
        metadata = {}
        for line in frontmatter.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("\"'")
        return {"name": metadata["name"], "description": metadata["description"],
                "body": body.strip(), "dir": path.parent}

    def get_skill_content(self, name):
        skill = self.skills[name]
        content = f"# Skill: {skill['name']}\n\n{skill['body']}"
        # Append Layer 3 hints (list available resources)
        for folder in ["scripts", "references", "assets"]:
            folder_path = skill["dir"] / folder
            if folder_path.exists():
                files = list(folder_path.glob("*"))
                if files:
                    content += f"\n\n- {folder}: {', '.join(f.name for f in files)}"
        return content
```

## Cache-Preserving Injection

This is the critical design detail. Skill content goes into `tool_result` (a user message), NOT the system prompt:

```sh
Wrong: Edit system prompt -> prefix changes -> cache invalidated -> 20-50x cost
Right: Return as tool_result -> prefix unchanged -> cache hit -> cost-efficient
```

```python
def run_skill(skill_name):
    content = SKILLS.get_skill_content(skill_name)
    return f'<skill-loaded name="{skill_name}">\n{content}\n</skill-loaded>\n\n'
           f'Follow the instructions in the skill above.'
```

The `<skill-loaded>` tags tell the model this is skill content, not a tool output. The model now "knows" how to do the task.

## Progressive Disclosure

| Layer | What | When | Cost |
|-------|------|------|------|
| 1. Metadata | name + description | Always (system prompt) | ~100 tokens/skill |
| 2. Body | Full SKILL.md instructions | On trigger (Skill tool) | ~2000 tokens |
| 3. Resources | scripts, references, assets | As needed (read_file/bash) | Unlimited |

This keeps context lean while allowing arbitrary depth. A skill with a 50-page reference doc costs nothing until the model actually needs it.

## System Prompt Integration

```python
SYSTEM = f"""You are a coding agent at {WORKDIR}.

**Skills available** (invoke with Skill tool when task matches):
{SKILLS.get_descriptions()}
    # -> "- pdf: Process PDF files. Use when reading, creating, or merging PDFs."
    # -> "- mcp: Build MCP servers..."

**Subagents available** (invoke with Task tool for focused subtasks):
{get_agent_descriptions()}

Rules:
- Use Skill tool IMMEDIATELY when a task matches a skill description
..."""
```

The model sees skill descriptions in every turn (Layer 1, cheap). When a task matches, it calls `Skill(skill="pdf")` to load the full instructions (Layer 2).

## The Deeper Insight

> **Knowledge externalization: teach the model by writing files, not by training.**

Traditional AI: knowledge locked in model parameters. To teach new skills: collect data, train, deploy. Cost: $10K-$1M+, timeline: weeks.

Skills: knowledge stored in editable files. To teach new skills: write a SKILL.md file. Cost: free, timeline: minutes. Anyone can do it.

## Hook Events

The production system supports 15 hook event types (PreToolUse, PostToolUse,
PostToolUseFailure, Notification, UserPromptSubmit, SessionStart, SessionEnd,
Stop, SubagentStart, SubagentStop, PreCompact, PermissionRequest, Setup,
TeammateIdle, TaskCompleted). Our implementation focuses on the core PreToolUse
and PostToolUse patterns. Hooks are shell commands executed in response to
specific events, enabling extensibility without changing core code.

---

**Tools give capability. Skills give expertise.**

[<-- v3](./v3-subagent-mechanism.md) | [Back to README](../README.md) | [v5 -->](./v5-context-compression.md)
