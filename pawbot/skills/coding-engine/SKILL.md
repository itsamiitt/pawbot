---
name: coding-engine
description: "Advanced coding tools via the coding MCP server: index codebases for semantic search, create/restore code checkpoints, scaffold new projects, and analyze code structure. Use when the user asks to search code semantically, checkpoint work, generate boilerplate, or analyze a project's architecture."
metadata: {"pawbot":{"emoji":"⚙️","requires":{}}}
---

# Coding Engine

Use the `coding` MCP server for advanced coding operations.

## Core Tools

| Tool | Purpose |
|------|---------|
| `code_index_project` | Build a semantic index of a project for fast search |
| `code_search` | Search indexed code semantically (natural language) |
| `code_checkpoint` | Save a named snapshot of current code state |
| `code_restore` | Restore code to a previous checkpoint |
| `code_list_checkpoints` | List all saved checkpoints |
| `code_scaffold` | Generate project boilerplate |
| `code_analyze` | Analyze project structure and dependencies |

## Workflow: Index and Search

```
mcp_coding_code_index_project(path="/home/user/my-project")
mcp_coding_code_search(query="authentication middleware", limit=10)
```

## Workflow: Safe Refactoring

1. Create a checkpoint before changes:
```
mcp_coding_code_checkpoint(name="before-refactor", path="/home/user/my-project")
```

2. Make changes using regular file tools

3. If something breaks, restore:
```
mcp_coding_code_restore(name="before-refactor")
```

## Workflow: New Project

```
mcp_coding_code_scaffold(template="fastapi", name="my-api", path="/home/user/projects")
```

## Tips

- Always checkpoint before risky refactors
- Re-index after significant code changes
- Code search uses SQLite indexes stored in `~/.pawbot/code-indexes/`
- Checkpoints are stored in `~/.pawbot/checkpoints/`
