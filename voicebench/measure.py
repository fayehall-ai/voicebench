"""Shared measurement types. No provider or network code here."""

from __future__ import annotations

import contextlib
import io
import time
from dataclasses import dataclass, field


@dataclass
class Sample:
    """One call.

    spoken_ms is time until the caller could hear ANY token. It is None on
    blocking rows because nothing is speakable until the call returns, and
    reporting a number there would be inventing data.
    """
    spoken_ms: float | None
    total_ms: float
    tokens: int = 0
    chars: int = 0
    tool_fired: bool = False
    cache_read: int = 0


@dataclass(frozen=True)
class Cell:
    """One coordinate in the measurement space."""
    provider: str
    model: str
    scenario: str
    path: str                     # "blocking" | "streaming"

    def label(self) -> str:
        return (f"{self.provider:<16} {self.model:<11} "
                f"{self.scenario:<17} {self.path}")

    def as_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model,
                "scenario": self.scenario, "path": self.path}


@dataclass
class Result:
    """Aggregated samples for one cell."""
    spoken_p50: float | None
    total_p50: float
    total_p95: float
    tokens: float
    chars: float
    fired: int
    n: int
    cache_read: float = 0.0
    samples: list[Sample] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "spoken_p50": self.spoken_p50,
            "total_p50": self.total_p50,
            "total_p95": self.total_p95,
            "tokens": self.tokens,
            "chars": self.chars,
            "tool_fired_runs": self.fired,
            "n": self.n,
            "cache_read": self.cache_read,
            "raw_samples": [
                {"spoken_ms": s.spoken_ms, "total_ms": s.total_ms,
                 "tokens": s.tokens, "chars": s.chars,
                 "tool_fired": s.tool_fired, "cache_read": s.cache_read}
                for s in self.samples
            ],
        }


class Clock:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000


@contextlib.contextmanager
def quiet():
    """Frameworks that print by default would put that write in the timer."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q / 100 * (len(ordered) - 1))))]


def eligibility(member_id: str, date_of_service: str) -> dict:
    """Tool stub. Returns instantly so the measurement isolates model time."""
    return {"active": True, "plan": "Choice Plus"}
