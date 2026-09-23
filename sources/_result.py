"""Every source reports how it went, not just what it found.

An empty list from a rate-limited API and an empty list from a company with no
news are the same value and opposite meanings. Downstream must be able to tell
them apart, so status travels with the data.
"""

from dataclasses import dataclass, field

OK = "ok"
RATE_LIMITED = "rate_limited"
NO_KEY = "no_key"
NO_COVERAGE = "no_coverage"
UNRESOLVED = "unresolved"
ERROR = "error"


@dataclass(frozen=True)
class SourceResult:
    status: str
    data: list | dict = field(default_factory=list)
    detail: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """True only when the source actually answered."""
        return self.status == OK

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail, "meta": self.meta, "data": self.data}
