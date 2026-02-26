# s05: Skills (技能加载)

`s01 > s02 > s03 > s04 > [ s05 ] s06 | s07 > s08 > s09 > s10 > s11 > s12`

> *"Load on demand, not upfront"* -- 知识通过 tool_result 按需注入, 别塞进 system prompt。

## 问题

你希望智能体遵循特定领域的工作流: git 约定、测试模式、代码审查清单。全塞进系统提示太浪费 -- 10 个技能, 每个 2000 token, 就是 20,000 token, 大部分跟当前任务毫无关系。

## 解决方案

```
System prompt (Layer 1 -- always present):
+--------------------------------------+
| You are a coding agent.              |
| Skills available:                    |
|   - git: Git workflow helpers        |  ~100 tokens/skill
|   - test: Testing best practices     |
+--------------------------------------+

When model calls load_skill("git"):
+--------------------------------------+
| tool_result (Layer 2 -- on demand):  |
| <skill name="git">                   |
|   Full git workflow instructions...  |  ~2000 tokens
|   Step 1: ...                        |
| </skill>                             |
+--------------------------------------+
```

第一层: 系统提示中放技能名称 (低成本)。第二层: tool_result 中按需放完整内容。

## 工作原理

1. 技能文件以 Markdown 格式存放在 `.skills/`, 带 YAML frontmatter。

```
.skills/
  git.md       # ---\n description: Git workflow\n ---\n ...
  test.md      # ---\n description: Testing patterns\n ---\n ...
```

2. SkillLoader 解析 frontmatter, 分离元数据和正文。

```python
class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills = {}
        for f in sorted(skills_dir.glob("*.md")):
            text = f.read_text()
            meta, body = self._parse_frontmatter(text)
            self.skills[f.stem] = {"meta": meta, "body": body}

    def get_descriptions(self) -> str:
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "")
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'."
        return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"
```

3. 第一层写入系统提示。第二层不过是 dispatch map 中的又一个工具。

```python
SYSTEM = f"""You are a coding agent at {WORKDIR}.
Skills available:
{SKILL_LOADER.get_descriptions()}"""

TOOL_HANDLERS = {
    # ...base tools...
    "load_skill": lambda **kw: SKILL_LOADER.get_content(kw["name"]),
}
```

模型知道有哪些技能 (便宜), 需要时再加载完整内容 (贵)。

## 相对 s04 的变更

| 组件           | 之前 (s04)       | 之后 (s05)                     |
|----------------|------------------|--------------------------------|
| Tools          | 5 (基础 + task)  | 5 (基础 + load_skill)          |
| 系统提示       | 静态字符串       | + 技能描述列表                 |
| 知识库         | 无               | .skills/*.md 文件              |
| 注入方式       | 无               | 两层 (系统提示 + result)       |

## 试一试

```sh
cd learn-claude-code
python agents/s05_skill_loading.py
```

试试这些 prompt (英文 prompt 对 LLM 效果更好, 也可以用中文):

1. `What skills are available?`
2. `Load the agent-builder skill and follow its instructions`
3. `I need to do a code review -- load the relevant skill first`
4. `Build an MCP server using the mcp-builder skill`
