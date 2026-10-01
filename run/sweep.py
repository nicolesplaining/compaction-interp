"""Gate B: the same claim in a compaction summary vs. in a user turn.

GOAL
    Establish that there are conditions under which a claim carried by a
    genuine compaction summary is believed MORE than the same claim carried by
    an ordinary user message, and characterize them.

HYPOTHESIS, fixed before the first run
    H1  uptake(compaction) > uptake(user)
        A claim the model reads out of a compaction summary is acted on more
        readily, and hedged less, than the same claim read out of a user turn.

    Direction is predicted, so a two-sided test is not what is being asked;
    but the sign that comes back is the sign that gets reported. The pilot
    cuts both ways and that is why this runs: a forged transcript put the user
    turn AHEAD of compaction, while the fidelity-preserving `seed` method put
    compaction far ahead. The forged arm lacked the isCompactSummary marking,
    so `seed` is the arm to trust and H1 is the live prediction.

    H2  the gap widens as the claim gets less plausible
        A user asserting something absurd invites pushback; a summary
        asserting it reads as settled, and settled things are not re-argued.

ARMS
    compaction  the claim joins the conversation before it is summarized, so
                core's own summarizer carries it into a properly marked
                summary (placement=seed in the mod)
    user        the same text as a user turn adjacent to the summary
    assistant   the same text as an assistant turn adjacent to the summary;
                separates "compaction is trusted" from "my own past words are
                trusted"
    none        no claim; establishes what the model says anyway, without
                which an arm's uptake cannot be read as uptake

WHAT IS HELD CONSTANT
    Every arm compacts. Every arm runs the same seeding turn, the same probe,
    the same model, tools refused, MCP off, and a fresh working directory.
    The arms differ in where the claim sits and nothing else.

EXPLORATORY STATUS
    This is the exploratory half. Cells that show the effect get re-run on
    held-out claims before any of it is called confirmed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from probe import trial

ARMS = ("compaction", "user", "assistant", "none")

# The mod speaks in placements; the experiment speaks in arms. `compaction` is
# `seed` because seeding before the summarizer runs is the only placement that
# leaves the summary marked as one.
PLACEMENT_OF = {
    "compaction": "seed",
    "user": "user",
    "assistant": "assistant",
    "none": "none",
}


def load_claims(path: Path) -> list[dict]:
    """Reads the claim ladder: one JSON object per line."""
    claims = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            claims.append(json.loads(line))
    return claims


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claims", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--arms", nargs="+", default=list(ARMS), choices=ARMS)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--attempts", type=int, default=3,
                    help="tries per cell before the cell is dropped")
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--limit", type=int, help="first N claims only, for a smoke run")
    args = ap.parse_args()

    claims = load_claims(args.claims)
    if args.limit:
        claims = claims[: args.limit]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    dropped = 0
    total = len(claims) * len(args.arms) * args.reps
    spent = 0.0

    with args.out.open("a") as fh:
        for rep in range(args.reps):
            for claim in claims:
                for arm in args.arms:
                    done += 1
                    # Runs flake: a turn can die on a malformed-tool-use loop
                    # or a transient API error. Each attempt is a fresh session
                    # in a fresh directory, so a retry is clean rather than a
                    # resumption of a half-finished trial.
                    row = None
                    last = None
                    for attempt in range(args.attempts):
                        try:
                            row = trial(
                                claim=claim["text"],
                                placement=PLACEMENT_OF[arm],
                                seed=claim.get("seed_prompt") or (
                                    "In one short sentence, what is a sensible way "
                                    "to organize build notes for a small project?"),
                                probe=claim["probe"],
                                key=claim.get("key"),
                                model=args.model,
                                tools=args.tools,
                                claim_id=claim["id"],
                            )
                            row["attempts"] = attempt + 1
                            break
                        except Exception as exc:
                            last = exc
                    if row is None:
                        dropped += 1
                        print(f"[{done}/{total}] {claim['id']} {arm}: DROPPED "
                              f"after {args.attempts} {last}", file=sys.stderr)
                        continue

                    row["arm"] = arm
                    row["rep"] = rep
                    row["level"] = claim.get("level")
                    row["domain"] = claim.get("domain")

                    # An arm that was supposed to carry the claim and did not
                    # is not a null result, it is a broken run. Marking it here
                    # keeps it out of the uptake numbers.
                    a = row["audit"]
                    row["usable"] = bool(
                        a["summary_found"] and (
                            arm == "none"
                            or a["claim_in_summary"] or a["key_in_summary"]
                            or arm in ("user", "assistant")
                        )
                    )

                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    spent += row.get("cost_usd") or 0.0
                    mark = "" if row["usable"] else "  UNUSABLE"
                    print(f"[{done}/{total}] {claim['id']} {arm} "
                          f"${spent:.2f}{mark}")

    print(f"\nwrote {args.out}  dropped={dropped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
