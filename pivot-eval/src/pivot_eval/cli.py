"""typer CLI: pivot-eval run | report | suggest."""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .config import DATASET_DEFAULT, RUNS_DIR
from .dataset import filter_cases, load_dataset
from .reporter import write_run
from .runner import run_cases


app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Read-only eval runner for the Pivot chatbot.")
console = Console()


@app.command()
def run(
    dataset: Path = typer.Option(
        DATASET_DEFAULT, "--dataset", "-d",
        help="Path to the eval dataset Markdown file.",
    ),
    filter_: str = typer.Option(
        None, "--filter", "-f",
        help="Comma-separated category prefixes or specific case IDs.",
    ),
    limit: int = typer.Option(None, "--limit", "-n",
                              help="Cap the number of cases run."),
    sequential: bool = typer.Option(False, "--sequential",
                                    help="Disable parallel execution (debugging)."),
):
    """Run the eval suite end-to-end. Writes runs/<ts>/."""
    cases = load_dataset(dataset)
    cases = filter_cases(cases, filter_expr=filter_, limit=limit)
    if not cases:
        console.print("[red]no cases matched the filter[/red]")
        raise typer.Exit(2)
    console.print(f"[bold blue]running {len(cases)} cases[/bold blue]")
    results = run_cases(cases, sequential=sequential)
    run_dir = write_run(results)
    console.print(f"[green]ok[/green] artefacts written to: {run_dir}")
    summary = {
        "pass": sum(1 for r in results if r.score and r.score.verdict == "pass"),
        "partial": sum(1 for r in results if r.score and r.score.verdict == "partial"),
        "fail": sum(1 for r in results if r.score and r.score.verdict == "fail"),
        "error": sum(1 for r in results if r.score and r.score.verdict == "error"),
    }
    console.print_json(data={"total": len(results), **summary,
                             "run_dir": str(run_dir)})


@app.command()
def report(
    run_dir: Path = typer.Option(
        None, "--run", help="Specific run directory; default: latest."
    ),
):
    """Re-emit report.md / conversations.md from an existing results.json."""
    target = run_dir or _latest_run_dir()
    import json
    from .reporter import _conversations_md, _report_md
    data = json.loads((target / "results.json").read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    # The report regen needs EvalResult-shaped objects; just rewrite from cases.
    from types import SimpleNamespace as N
    results = []
    for c in data["cases"]:
        transcript = [
            N(user=t["user"], response_text=t["response"],
              tools_called=[N(name=x["name"], args=x.get("args", {}))
                            for x in t["tools"]],
              intent=t.get("intent"), latency_ms=t.get("latency_ms", 0))
            for t in c["transcript"]
        ]
        scoring = c.get("scoring") or {}
        score_obj = N(
            verdict=c["verdict"],
            criteria=[
                N(name=cc["name"], kind=cc["kind"], score=cc["score"],
                  deterministic=cc["deterministic"], rationale=cc.get("rationale", ""))
                for cc in scoring.get("criteria", [])
            ],
            violations=scoring.get("violations", []),
            notes=scoring.get("notes", []),
        )
        results.append(N(case_id=c["id"], category=c["category"],
                         is_multi_turn=c.get("is_multi_turn", False),
                         transcript=transcript, score=score_obj,
                         error=c.get("error")))
    (target / "conversations.md").write_text(
        _conversations_md(results, summary, target.name), encoding="utf-8",
    )
    (target / "report.md").write_text(
        _report_md(summary, results, target.name), encoding="utf-8",
    )
    console.print(f"[green]ok[/green] regenerated reports in {target}")


@app.command()
def suggest(
    run_dir: Path = typer.Option(
        None, "--run", help="Specific run directory; default: latest."
    ),
):
    """Read the latest run, write suggestions.md (read-only — no patches)."""
    from .suggester import write_suggestions
    out = write_suggestions(run_dir)
    console.print(f"[green]ok[/green] {out}")


@app.command()
def list_cases(
    dataset: Path = typer.Option(DATASET_DEFAULT, "--dataset", "-d"),
    filter_: str = typer.Option(None, "--filter", "-f"),
):
    """Print case IDs that match a filter (no execution)."""
    cases = load_dataset(dataset)
    cases = filter_cases(cases, filter_expr=filter_)
    for c in cases:
        kind = "MT" if c.is_multi_turn else "ST"
        preview = (c.input if not c.is_multi_turn else " | ".join(c.turns))[:60]
        console.print(f"{c.id:14} {kind} {preview}")


def _latest_run_dir() -> Path:
    candidates = sorted(
        (p for p in RUNS_DIR.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    if not candidates:
        raise SystemExit("no runs found — run `pivot-eval run` first")
    return candidates[-1]


if __name__ == "__main__":
    app()
