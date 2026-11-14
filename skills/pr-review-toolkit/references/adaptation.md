# Source and dependencies

This is a project-local Codex adaptation of Anthropic `pr-review-toolkit` version 1.0.1, not an unmodified Claude plugin or a performance-equivalence certification. Modified 2026-11-03. Original authors are Anthropic; the source README credits Daisy. The original Apache-2.0 LICENSE is included unchanged. The source README's MIT label conflicts with its actual LICENSE; this adaptation retains the latter rather than relabeling it MIT.

Source repository: https://github.com/anthropics/claude-plugins-official
Revision: `a7ba5b93db0c7c8d17f3f1d25853adca79598a1a`
Source directory: `plugins/pr-review-toolkit/`

## What is installed

- `commands/review-pr.md` → `SKILL.md` and `references/execution.md`.
- All six `agents/*.md` → same-named reference procedures. Their full analytical frameworks and distinct output scales are retained; Claude-only frontmatter/persona triggers are removed.
- Source agent `code-reviewer`'s ≥80 confidence filter is retained; confidence bands describe evidence strength, and severity is separate from confidence. Test criticality and type-dimension ratings retain 1–10 scales.
- The six bundled specialist references are internal procedures. No separately installed agent or plugin is assumed. Tests/builds use the supplied scope and execution authorization.
- Sentry/Statsig/logForDebugging/errorIds and React/ES-module/function-style examples are target-project assumptions in source agent text, not libraries to install. They were replaced by actual scoped project policies. Strict silent-failure language is applied to observable failures, not every legitimate optional return or error propagation.
- Simplification during review is advisory; an authorized refinement task may apply edits and validate them. No automatic source rewrite on skill discovery.

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
