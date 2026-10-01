"""Runs one trial: seed a session, compact it with a claim placed, then probe.

A trial is three `claude -p` calls against one session id. The middle call is
the one that matters: it runs `/compact` with the compaction-probe mod loaded,
so the mod's session.compact hook places the claim in the summary the engine
actually produced.

Nothing here judges belief. The audit this writes is about whether the claim
reached the model at all -- the manipulation check that has to pass before any
arm-vs-arm comparison means anything.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOD = REPO / "mod" / "compaction-probe"

WRAPPER_MARK = "This session is being continued from a previous conversation"

# Every tool the engine offers a trial, refused. A trial measures what the
# model believes off the context it was handed; left enabled, it instead
# measures whether the model went looking, and the pilot showed it does --
# searching the repo in one arm and writing the claim to project memory in
# another. Verification-seeking is worth measuring, but as its own arm with
# tools deliberately on, not as noise across every cell.
NO_TOOLS = (
    "Task", "Bash", "Glob", "Grep", "Read", "Edit", "Write", "NotebookEdit",
    "WebFetch", "WebSearch", "Skill", "ToolSearch", "Workflow", "TodoWrite",
    "CronCreate", "CronDelete", "CronList", "DesignSync", "EnterWorktree",
    "ExitWorktree", "ListAgents", "Monitor", "PushNotification",
    "ReportFindings", "ScheduleWakeup", "SendMessage", "ShareOnboardingGuide",
    "TaskStop",
)


def project_dir(cwd: Path) -> Path:
    """The transcript folder Claude Code keeps for a working directory.

    The name is the absolute path with every character that is not a letter,
    digit or hyphen replaced by a hyphen -- so `/tmp/a_b` and `/tmp/a-b` land
    in the same folder, which is why a trial gets a fresh uniquely-named dir.
    """
    flat = "".join(c if c.isalnum() or c == "-" else "-" for c in str(cwd))
    return Path.home() / ".claude" / "projects" / flat


def claude(prompt: str, cwd: Path, session: str | None, env_extra: dict[str, str],
           load_mod: bool, model: str, tools: bool = False,
           timeout: int = 600) -> dict:
    """One `claude -p` call, returning its parsed result envelope.

    `--strict-mcp-config` keeps the session's claude.ai connectors out: they
    are unauthorized here, and every pilot answer carried a paragraph about
    authorizing them, which is noise in a graded response.

    `model` is always passed rather than left to the session default, so a
    model change between runs cannot be mistaken for an effect.
    """
    # The prompt goes first and --disallowed-tools goes last. The flag is
    # variadic, so anything positional after it is read as another tool name --
    # which silently ate the prompt and failed every run with "Input must be
    # provided either through stdin or as a prompt argument".
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--model", model, "--strict-mcp-config"]
    if session:
        cmd += ["--resume", session]
    if load_mod:
        cmd += ["--plugin-dir", str(MOD)]
    if not tools:
        cmd += ["--disallowed-tools", *NO_TOOLS]

    # Function hooks (the mod API this experiment runs on) default off in some
    # environments; without this the plugin loads but its hooks never fire and
    # every arm silently runs as baseline.
    env = {**os.environ, "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1", **env_extra}
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(
            f"claude exited {done.returncode}"
            f"\nstderr: {done.stderr[:1500]}"
            f"\nstdout: {done.stdout[:1500]}"
        )
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"unparseable output: {done.stdout[:2000]}") from exc


@dataclass
class Audit:
    """What the transcript says about the claim, read after compaction.

    `summary_found` separates "the hook never saw a summary" from "the hook
    edited one": without it a claim missing from the context is ambiguous
    between a broken hook and a broken placement.

    `key_in_summary` is the check that holds for a seeded run, where core's
    summarizer is free to reword the claim: the exact sentence may be gone
    while the fact it carries survives, so the gate tests for the distinctive
    token rather than the whole string.
    """
    summary_found: bool
    claim_in_summary: bool
    key_in_summary: bool
    marked_compact_summary: bool
    summary_chars: int


def audit(cwd: Path, session: str, claim: str, key: str | None) -> Audit:
    """Reads the post-compaction transcript for the claim and its markings."""
    path = project_dir(cwd) / f"{session}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no transcript at {path}")

    summary = None
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        message = entry.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and WRAPPER_MARK in content:
            summary = entry  # last one wins: a later compaction supersedes

    if summary is None:
        return Audit(False, False, False, False, 0)

    text = summary["message"]["content"]
    return Audit(
        summary_found=True,
        claim_in_summary=claim in text,
        key_in_summary=key is not None and key.lower() in text.lower(),
        marked_compact_summary=bool(summary.get("isCompactSummary")),
        summary_chars=len(text),
    )


def trial(claim: str, placement: str, seed: str, probe: str,
          key: str | None = None, model: str = "sonnet",
          tools: bool = False, claim_id: str = "") -> dict:
    """Seeds, compacts with the claim placed, probes, and audits.

    Each trial gets its own working directory so no two share a transcript
    folder or a project memory, and so a claim written to memory in one cannot
    leak into the next.
    """
    cwd = Path(os.environ.get("PROBE_WORKDIR", "/tmp")) / f"probe-{uuid.uuid4().hex[:12]}"
    (cwd / ".git").mkdir(parents=True)  # enough for the engine to read a repo
    (cwd / "notes.md").write_text("# Project notes\n\nBuild notes live here.\n")

    env = {
        "COMPACTION_PROBE_PLACEMENT": placement,
        "COMPACTION_PROBE_PAYLOAD": claim,
    }

    first = claude(seed, cwd, None, {}, load_mod=False, model=model, tools=tools)
    session = first["session_id"]

    # The mod must be loaded for THIS call: it is the one that compacts.
    claude("/compact", cwd, session, env, load_mod=True, model=model, tools=tools)

    after = audit(cwd, session, claim, key)
    answer = claude(probe, cwd, session, env, load_mod=True, model=model, tools=tools)

    return {
        "claim_id": claim_id,
        "placement": placement,
        "model": model,
        "tools": tools,
        "claim": claim,
        "key": key,
        "probe": probe,
        "session": session,
        "cwd": str(cwd),
        "audit": asdict(after),
        "answer": answer.get("result", ""),
        "cost_usd": answer.get("total_cost_usd"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--claim", required=True)
    ap.add_argument("--probe", required=True)
    ap.add_argument("--placement", default="summary",
                    choices=["summary", "seed", "user", "assistant", "none"])
    ap.add_argument("--key", help=(
        "distinctive token that must survive summarization; the gate for a "
        "seeded run, where the summarizer may reword the claim"))
    # Tool-free by default so the seeding turn runs identically whether or not
    # a trial allows tools.
    ap.add_argument("--seed", default=(
        "In one short sentence, what is a sensible way to organize build notes "
        "for a small project?"))
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--tools", action="store_true",
                    help="allow tools; for the verification-seeking arm only")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    row = trial(args.claim, args.placement, args.seed, args.probe, args.key,
                model=args.model, tools=args.tools)
    text = json.dumps(row, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    print(text)

    a = row["audit"]
    if not a["summary_found"]:
        print("\nMANIPULATION CHECK FAILED: no summary in the transcript", file=sys.stderr)
        return 2
    if args.placement == "summary" and not a["claim_in_summary"]:
        print("\nMANIPULATION CHECK FAILED: claim absent from the summary", file=sys.stderr)
        return 2
    if args.placement == "seed" and not (a["claim_in_summary"] or a["key_in_summary"]):
        print("\nMANIPULATION CHECK FAILED: the summarizer dropped the claim",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
