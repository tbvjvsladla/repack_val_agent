# Source and dependencies

This is a project-local Codex adaptation of Anthropic `code-review` version 1.0.1, not an unmodified Claude plugin or a performance-equivalence certification. Modified 2026-11-03. Original authors are Anthropic; the source README credits Boris Cherny. The original Apache-2.0 LICENSE is included unchanged.

Source repository: https://github.com/anthropics/claude-plugins-official
Revision: `a7ba5b93db0c7c8d17f3f1d25853adca79598a1a`
Source directory: `plugins/code-review/`

## What is installed

- `commands/code-review.md` → `SKILL.md` and `references/workflow.md`.
- Source command's 2 independent guideline + 2 bug passes and separate candidate validation are retained, with available-slot scheduling. Claude haiku/sonnet/opus selection becomes native Codex model/effort inheritance; no forced model downgrade or account setting change.
- README's history-analysis/≥80 scoring description differs from the current command, which uses high-signal validation. The command is the execution basis; this discrepancy is explicit, not silently merged.
- `gh` is needed for the native live GitHub PR path; supplied PR snapshots or explicitly local diffs can be reviewed without it. GitHub inline publishing additionally needs a supported tool, named `mcp__github_inline_comment__create_inline_comment` in the source command.
- Source has no bundled `.mcp.json` or MCP server implementation supplying that inline tool. No new MCP or credentials are installed. Offline/local review is fully specified and is not reported as live GitHub validation.

## Dependency audit and Codex boundary

The source plugin.json and marketplace entry have no dependency declarations. The source package includes no hooks, scripts, package manifests, installed skills or bundled MCP servers. The command's `Task`/agent references are Claude host behavior; adapted reviews explicitly load the procedures into native Codex subagent tasks. No additional plugin, shared sibling skill, external Python package, or global setting is required for local review. Git and the selected host's file/search/subagent tools are the runtime capabilities; real tool errors remain visible.

These reference procedures are not `.codex/agents` registrations. No project or global model/permission configuration is overwritten. A read-only reviewer instruction is behavioral scope, not an OS sandbox. The source seed remains untouched and is not needed after installation.

Scoped AGENTS.md and referenced project rules are observed; source CLAUDE.md checks are retained for projects that have them. This entry point accepts its own review input and returns findings with evidence and execution limits.

Official references checked 2026-11-03:

- [Claude plugin migration](https://developers.openai.com/plugins/guides/submit-claude-plugin): convert command/agent procedures into skills and keep relevant resources.
- [Codex local skills](https://learn.chatgpt.com/docs/build-skills): repository `.agents/skills`, metadata and automatic discovery.
- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): scoped parallel read-heavy tasks, inherited model/effort and parent permissions.

Installation/discovery tests establish packaging and selected behavior. Matching Claude review accuracy/recall, costs and latency requires a separate comparative benchmark and is not claimed here.

Modified 2026-11-04: removed cross-tool selection and orchestration guidance while retaining the review procedures and original license.
