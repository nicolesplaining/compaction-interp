# Vendored reference: anthropics/claude-code

Source: https://github.com/anthropics/claude-code.git
Commit: `6160717d8994f236ab381cf534fc1cde5347c12f`
Committed: 2026-09-30T22:23:04-07:00
Vendored: 2026-09-30

Reference only — nothing here is built or imported at runtime. Kept in-tree so
the plugin API surface this project targets is pinned to a known version.

## Why these paths

| Path | Why |
| --- | --- |
| `claude-code/mods/types/claude-code.d.ts` | The plugin API declarations. Defines `session.compact`, `SessionMessage`, `SessionCompactInput/Result` — the surface the experiment's mod is written against. |
| `claude-code/mods/{telemetry,diff,agents-md,sec-default}` | The four mods built into the binary, published as source. Used as working templates for plugin layout, `register()` shape, and `claude plugin test` conventions. |
| `claude-code/examples/{hooks,settings}` | Classic-hook and settings examples. |
| `claude-code/plugins/plugin-dev/skills/{hook-development,plugin-structure}` | Official guidance on hook and plugin structure. |

## Deliberately omitted

`demo.gif` (11 MB), `CHANGELOG.md` (872 KB), `feed.xml` (270 KB), the issue-triage
scripts under `scripts/`, and the rest of `plugins/` — none bear on this project.

## Note on what this repo is not

It is not the Claude Code engine source. The compaction prompt text and the
summary re-injection wrapper are not here; they live only in the compiled
binary. This project does not depend on them: the mod API's `session.compact`
hook exposes the compaction pipeline as middleware, which is a supported
interface and the one the experiment targets.

To refresh:

    git clone --depth 1 https://github.com/anthropics/claude-code.git
