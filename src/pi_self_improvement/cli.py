"""Command-line surface (REQ-001, REQ-015, REQ-016).

Two flows share one entry point: a scan, and the resolutions subcommands that
record what a reviewer decided. Nothing here applies a proposal — REQ-001 puts a
human between staging and any change to a skill, a memory file or source code,
so this program only ever writes under its own output root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import detect, parse, route, stage, state, writer
from .config import DEFAULT_OUTPUT_ROOT, Config, ConfigError

EXIT_OK = 0
EXIT_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pi-self-improvement",
        description="Mine pi session transcripts for friction and stage review proposals.",
    )
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--since-days", type=float, default=7.0, metavar="N")
    window.add_argument(
        "--all",
        action="store_true",
        dest="scan_all",
        help="scan every session regardless of age (backfill; never automatic)",
    )
    parser.add_argument("--max-sessions", type=int, default=None, metavar="N")
    parser.add_argument("--dry-run", action="store_true", help="print only; write nothing")
    parser.add_argument(
        "--full", action="store_true", help="keep excerpts unredacted; marks output local-only"
    )
    parser.add_argument("--include-seen", action="store_true")
    parser.add_argument("--include-resolved", action="store_true")
    parser.add_argument("--home", default=None, metavar="PATH")
    parser.add_argument("--output-root", default=None, metavar="PATH")
    parser.add_argument("--config", default=None, metavar="PATH")
    parser.add_argument("--machine", default=None, metavar="NAME")

    parser.add_argument("--resolve", metavar="ROUTE:TARGET")
    parser.add_argument("--decision", choices=state.DECISIONS)
    parser.add_argument("--pr", default=None)
    parser.add_argument("--note", default=None)
    parser.add_argument("--by", default=None)
    parser.add_argument("--resolved-at", default=None, metavar="TS")
    parser.add_argument("--resolve-from", metavar="decisions.json")
    parser.add_argument(
        "--write-queue",
        metavar="TRIAGE_JSON",
        help="consume a headless triage result and write the queue (host-side writer)",
    )
    parser.add_argument("--list-resolutions", action="store_true")
    parser.add_argument("--unresolve", metavar="ROUTE:TARGET")
    return parser


def main(argv=None, *, stdout=None, stderr=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    try:
        config = Config.load(args.config, output_root=_output_root(args))
    except ConfigError as error:
        print(f"error: {error}", file=err)
        return EXIT_ERROR

    if args.write_queue:
        return _run_write_queue(args, out, err)
    if args.resolve or args.resolve_from or args.list_resolutions or args.unresolve:
        return _run_resolutions(args, out, err)
    return _run_scan(args, config, out, err)


def _run_write_queue(args, out, err) -> int:
    """The only path by which unattended triage reaches disk (ADR-0006)."""
    try:
        triage = writer.parse_triage(args.write_queue)
        result = writer.write_triage(_output_root(args), triage, machine=args.machine)
    except (writer.TriageError, writer.OutputRootEscape) as error:
        print(f"error: {error}", file=err)
        return EXIT_ERROR
    print(
        f"queued {result.queued}, dropped {result.dropped}; {result.queue_path}",
        file=out,
    )
    return EXIT_OK


def _output_root(args) -> Path:
    if args.output_root:
        return Path(args.output_root).expanduser()
    if args.home:
        return Path(args.home) / Path(DEFAULT_OUTPUT_ROOT).name
    return Path(DEFAULT_OUTPUT_ROOT).expanduser()


def _run_scan(args, config: Config, out, err) -> int:
    root = _output_root(args)
    redactor = config.redactor(full=args.full, home=args.home)

    discovered = parse.discover_transcripts(
        [parse.default_sessions_root(args.home)],
        since_days=None if args.scan_all else args.since_days,
        include_all=args.scan_all,
        max_sessions=args.max_sessions,
    )
    summaries, counts = parse.parse_transcripts(discovered)

    signals = []
    for summary in summaries:
        signals.extend(detect.detect_session(summary, redactor=redactor, config=config.detect))
    proposals = route.build_proposals(signals, config=config.route, redactor=redactor)

    store = state.State.load(root / state.STATE_FILE)
    resolutions = state.Resolutions.load(root / state.RESOLUTIONS_FILE)
    result = state.run_pipeline(
        proposals,
        state=store,
        resolutions=resolutions,
        include_seen=args.include_seen,
        include_resolved=args.include_resolved,
    )

    # REQ-018's third sink. The other two are written by stage.write_run.
    for warning in state.self_check(counts):
        print(f"warning: {warning}", file=err)

    if args.dry_run:
        # DEC-017: no state, no output files, no logs. stdout only.
        print(
            stage.render_packet(
                result.staged, "dry-run", counts, redactor.local_only, machine=args.machine
            ),
            file=out,
        )
        print(_summary_line(result, counts, dry_run=True), file=out)
        return EXIT_OK

    written = stage.write_run(
        root,
        result.staged,
        counts=counts,
        redactor=redactor,
        machine=args.machine,
    )
    store.record(result.staged, written.run_id)
    store.save(root / state.STATE_FILE)

    print(f"run {written.run_id}", file=out)
    print(_summary_line(result, counts, dry_run=False), file=out)
    print(f"packet: {written.packet_path}", file=out)
    return EXIT_OK


def _summary_line(result, counts, *, dry_run: bool) -> str:
    verb = "would stage" if dry_run else "staged"
    return (
        f"{verb} {len(result.staged)} proposal(s) "
        f"({len(result.regressions)} regression) from {counts.files} transcript(s); "
        f"suppressed {result.suppressed_resolved} resolved, {result.suppressed_seen} seen"
    )


def _run_resolutions(args, out, err) -> int:
    root = _output_root(args)
    path = root / state.RESOLUTIONS_FILE
    resolutions = state.Resolutions.load(path)
    store = state.State.load(root / state.STATE_FILE)

    if args.list_resolutions:
        if not resolutions.entries:
            print("no resolutions recorded", file=out)
        for key, entry in sorted(resolutions.entries.items()):
            suffix = f" ({entry.note})" if entry.note else ""
            print(f"{key}\t{entry.decision}\t{entry.resolved_at}{suffix}", file=out)
        return EXIT_OK

    if args.unresolve:
        if not resolutions.unresolve(args.unresolve):
            print(f"error: {args.unresolve} is not resolved", file=err)
            return EXIT_ERROR
        changed = [args.unresolve]
        verb = "unresolved"
    elif args.resolve_from:
        try:
            changed = resolutions.import_decisions(args.resolve_from, state=store)
        except ConfigError as error:  # pragma: no cover - defensive
            print(f"error: {error}", file=err)
            return EXIT_ERROR
        verb = "imported"
    else:
        if not args.decision:
            print("error: --resolve requires --decision", file=err)
            return EXIT_ERROR
        resolutions.resolve(
            args.resolve,
            args.decision,
            resolved_at=args.resolved_at,
            pr=args.pr,
            note=args.note,
            by=args.by,
            state=store,
        )
        changed = [args.resolve]
        verb = "resolved"

    resolutions.save(path)
    store.save(root / state.STATE_FILE)
    print(f"{verb} {len(changed)} target(s): {', '.join(changed) or '(none)'}", file=out)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
