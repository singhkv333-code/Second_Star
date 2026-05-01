"""Execute test cases. Sequential or parallel (thread pool — httpx is sync)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Iterable

from rich.console import Console
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn,
)

from .chatbot_client import ChatResponse, ChatbotClient, ToolCall
from .config import Settings, get_settings
from .dataset import MultiTurnCase, TestCase
from .judge import CaseScore, score


@dataclass
class TurnRecord:
    user: str
    response_text: str
    tools_called: list[ToolCall]
    intent: str | None
    latency_ms: int


@dataclass
class EvalResult:
    case_id: str
    category: str
    is_multi_turn: bool
    transcript: list[TurnRecord]
    score: CaseScore
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.case_id,
            "category": self.category,
            "is_multi_turn": self.is_multi_turn,
            "verdict": self.score.verdict if self.score else "error",
            "error": self.error,
            "transcript": [
                {
                    "user": t.user,
                    "response": t.response_text,
                    "tools": [{"name": x.name, "args": x.args} for x in t.tools_called],
                    "intent": t.intent,
                    "latency_ms": t.latency_ms,
                }
                for t in self.transcript
            ],
            "scoring": {
                "criteria": [
                    {
                        "name": c.name, "kind": c.kind, "score": c.score,
                        "deterministic": c.deterministic, "rationale": c.rationale,
                    }
                    for c in (self.score.criteria if self.score else [])
                ],
                "violations": self.score.violations if self.score else [],
                "notes": self.score.notes if self.score else [],
            },
        }


# ---- Runner -----------------------------------------------------------


def run_cases(
    cases: list[TestCase],
    *,
    sequential: bool = False,
    settings: Settings | None = None,
) -> list[EvalResult]:
    """Run every case; return results in input order."""
    settings = settings or get_settings()
    n = max(1, settings.pivot_eval_concurrency) if not sequential else 1

    console = Console()
    results_by_idx: dict[int, EvalResult] = {}

    with Progress(
        TextColumn("[bold blue]eval"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("·"),
        TimeElapsedColumn(),
        TextColumn("· {task.fields[label]}"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("running", total=len(cases), label="starting")

        if n == 1:
            client = ChatbotClient(settings)
            client.__enter__()
            try:
                for i, case in enumerate(cases):
                    res = _run_one(client, case)
                    results_by_idx[i] = res
                    progress.update(task, advance=1, label=f"{case.id} → {res.score.verdict if res.score else 'err'}")
            finally:
                client.__exit__(None, None, None)
        else:
            # Each worker thread keeps its own client (separate http session).
            with ThreadPoolExecutor(max_workers=n) as ex:
                futures = {
                    ex.submit(_worker, case, settings): i
                    for i, case in enumerate(cases)
                }
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        res = fut.result()
                    except Exception as e:
                        res = EvalResult(
                            case_id=cases[i].id,
                            category=cases[i].category,
                            is_multi_turn=cases[i].is_multi_turn,
                            transcript=[],
                            score=CaseScore(verdict="error"),
                            error=str(e)[:300],
                        )
                    results_by_idx[i] = res
                    progress.update(task, advance=1,
                                    label=f"{res.case_id} → "
                                          f"{res.score.verdict if res.score else 'err'}")

    return [results_by_idx[i] for i in range(len(cases))]


def _worker(case: TestCase, settings: Settings) -> EvalResult:
    with ChatbotClient(settings) as c:
        return _run_one(c, case)


def _run_one(client: ChatbotClient, case: TestCase) -> EvalResult:
    transcript: list[TurnRecord] = []
    last_resp: ChatResponse | None = None
    error: str | None = None

    cid = client.new_conversation()
    try:
        if isinstance(case, MultiTurnCase):
            inputs = list(case.turns)
        else:
            inputs = [case.input]
        for turn in inputs:
            resp = client.send(cid, turn)
            transcript.append(TurnRecord(
                user=turn,
                response_text=resp.text,
                tools_called=resp.tools_called,
                intent=resp.intent,
                latency_ms=resp.latency_ms,
            ))
            last_resp = resp
    except Exception as e:
        error = str(e)[:300]

    if last_resp is None:
        return EvalResult(
            case_id=case.id, category=case.category,
            is_multi_turn=case.is_multi_turn,
            transcript=transcript,
            score=CaseScore(verdict="error"),
            error=error,
        )

    case_score = score(case, last_resp)
    return EvalResult(
        case_id=case.id, category=case.category,
        is_multi_turn=case.is_multi_turn,
        transcript=transcript,
        score=case_score,
        error=error,
    )
