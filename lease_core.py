# SPDX-License-Identifier: MIT
"""Minimal check-then-lease algebra (copied, not imported).

Wang & Shi SkillEffect (arXiv:2608.17007) discrete contract:
proposal → independent checker rebuilds bounded IR → atomic capacity
lease → staged publish iff postcondition G. Dumping the program is inject.

Stdlib only. 
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

Row = Tuple[int, int, int]  # id, qty, ok
ROWS: Tuple[Row, ...] = (
    (1, 4, 1),
    (2, 1, 0),
    (3, 5, 1),
    (4, 2, 1),
    (5, 3, 0),
    (6, 6, 1),
    (7, 1, 1),
    (8, 2, 0),
)
N_ROWS = len(ROWS)
SUM_QTY = sum(r[1] for r in ROWS)  # 24
SUM_OK = sum(r[1] for r in ROWS if r[2] == 1)  # 18

R_RUN = 1
R_IO = 1
STAGED = 1
WINDOW = 2
B = 6
D_WINDOW = (1, 2)

EAGER_SRC = (
    "df = read_table(rows)\n"
    "out = df.filter(ok == 1).sum(qty)\n"
)
STREAM_SRC = (
    "df = scan_table(rows, window=2)\n"
    "out = df.filter(ok == 1).sum(qty)\n"
)

IR = Tuple[Tuple[Any, ...], ...]


def _digest(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


PLATFORM = {
    "R_run": R_RUN,
    "R_io": R_IO,
    "hash": _digest({"R_run": R_RUN, "R_io": R_IO, "vm": "toy-csv-v1"}),
}


@dataclass(frozen=True)
class Theta:
    family: str
    filter_col: str
    filter_val: int
    agg: str
    agg_col: str


@dataclass(frozen=True)
class Facts:
    n_rows: int
    schema: Tuple[str, ...]
    sum_qty: int
    n_ok: int
    input_hash: str


def S_csv(src: str) -> Optional[Theta]:
    if "read_table" not in src and "scan_table" not in src:
        return None
    if "filter" not in src or "ok" not in src:
        return None
    if "sum" not in src or "qty" not in src:
        return None
    return Theta("csv", "ok", 1, "sum", "qty")


def S_fasta(src: str) -> Optional[Theta]:
    if "SeqIO" in src or "fasta" in src.lower():
        return Theta("fasta", "ok", 1, "sum", "qty")
    return None


def I_csv(rows: Sequence[Row]) -> Facts:
    frozen = tuple(rows)
    return Facts(
        n_rows=len(frozen),
        schema=("id", "qty", "ok"),
        sum_qty=sum(r[1] for r in frozen),
        n_ok=sum(1 for r in frozen if r[2] == 1),
        input_hash=_digest(frozen),
    )


def L_csv(theta: Theta, facts: Facts, window: int, staged: int) -> IR:
    del facts
    return (
        ("scan", window),
        ("filter", theta.filter_col, theta.filter_val),
        ("sum", theta.agg_col),
        ("stage", staged),
    )


def A_csv(ir: IR) -> int:
    head = ir[0]
    if head[0] == "load":
        return int(head[1])
    if head[0] == "scan":
        return int(head[1])
    raise ValueError("unknown IR head")


def U_of(ir: IR, staged: int = STAGED) -> int:
    return R_RUN + R_IO + A_csv(ir) + staged


def G_csv(y: Optional[int], n_seen: int, facts: Facts) -> bool:
    if y is None:
        return False
    if n_seen != facts.n_rows:
        return False
    if type(y) is not int:
        return False
    return 0 <= y <= facts.sum_qty


@dataclass(frozen=True)
class Proposal:
    relation: str
    theta: Theta
    facts: Facts
    ir: IR
    U: int
    window: int
    staged: int
    binds: Tuple[str, ...]


@dataclass
class CheckResult:
    ok: bool
    reason: str
    rebuilt: Optional[IR] = None
    U: int = 0
    facts: Optional[Facts] = None
    theta: Optional[Theta] = None


@dataclass
class PlainLedger:
    """Atomic capacity ledger (parent check-then-lease). No witness."""

    cap: int = B
    active: Dict[int, int] = field(default_factory=dict)
    nxt: int = 0

    def charged(self) -> int:
        return sum(self.active.values())

    def acquire(self, u: int) -> Optional[int]:
        if self.charged() + u > self.cap:
            return None
        self.nxt += 1
        self.active[self.nxt] = u
        return self.nxt

    def release(self, lid: Optional[int]) -> None:
        if lid is None:
            return
        self.active.pop(lid, None)


def bind_hashes(src: str, facts: Facts) -> Tuple[str, ...]:
    return (
        _digest(src),
        facts.input_hash,
        PLATFORM["hash"],
        _digest("toy-csv-checker-v1"),
    )


def eager_load_ir(n: int) -> IR:
    return (("load", n), ("filter", "ok", 1), ("sum", "qty"), ("stage", STAGED))


def make_proposal(
    src: str,
    rows: Sequence[Row],
    window: int,
    *,
    relation: str = "csv",
    ir: Optional[IR] = None,
    U: Optional[int] = None,
    theta: Optional[Theta] = None,
    facts: Optional[Facts] = None,
    staged: int = STAGED,
) -> Proposal:
    facts = facts if facts is not None else I_csv(rows)
    theta = theta if theta is not None else S_csv(src)
    assert theta is not None
    ir = ir if ir is not None else L_csv(theta, facts, window, staged)
    U = U if U is not None else U_of(ir, staged)
    return Proposal(relation, theta, facts, ir, U, window, staged, bind_hashes(src, facts))


def check(z: Proposal, src: str, rows: Sequence[Row]) -> CheckResult:
    matches = []
    for name, rec in (("csv", S_csv), ("fasta", S_fasta)):
        th = rec(src)
        if th is not None:
            matches.append((name, th))
    if len(matches) != 1:
        return CheckResult(False, "no-unique-relation")
    name_hat, theta_hat = matches[0]
    if z.relation != name_hat:
        return CheckResult(False, "wrong-relation")
    facts_hat = I_csv(rows)
    if z.ir and z.ir[0][0] == "load":
        u_load = U_of(z.ir, z.staged)
        if u_load > B:
            return CheckResult(False, "over-cap", None, u_load, facts_hat, theta_hat)
    if z.window not in D_WINDOW or z.staged != STAGED:
        return CheckResult(False, "config-not-in-D")
    t_hat = L_csv(theta_hat, facts_hat, z.window, z.staged)
    u_hat = U_of(t_hat, z.staged)
    binds_hat = bind_hashes(src, facts_hat)
    if z.theta != theta_hat:
        return CheckResult(False, "theta-mismatch", t_hat, u_hat, facts_hat, theta_hat)
    if z.facts != facts_hat:
        return CheckResult(False, "facts-mismatch", t_hat, u_hat, facts_hat, theta_hat)
    if z.ir != t_hat:
        return CheckResult(False, "ir-mismatch", t_hat, u_hat, facts_hat, theta_hat)
    if z.U != u_hat:
        return CheckResult(False, "U-mismatch", t_hat, u_hat, facts_hat, theta_hat)
    if z.binds != binds_hat:
        return CheckResult(False, "bind-mismatch", t_hat, u_hat, facts_hat, theta_hat)
    if u_hat > B:
        return CheckResult(False, "over-cap", t_hat, u_hat, facts_hat, theta_hat)
    return CheckResult(True, "pass", t_hat, u_hat, facts_hat, theta_hat)


def run_vm(ir: IR, rows: Sequence[Row]) -> Tuple[int, int, int]:
    peak = 0
    acc = 0
    n_seen = 0
    head = ir[0]
    filt = ir[1]
    if head[0] == "load":
        live = len(rows)
        peak = max(peak, live + STAGED)
        for r in rows:
            n_seen += 1
            if r[2] == filt[2]:
                acc += r[1]
        return peak, acc, n_seen
    if head[0] == "scan":
        w = int(head[1])
        for i in range(0, len(rows), w):
            chunk = rows[i : i + w]
            live = len(chunk) + STAGED
            peak = max(peak, live)
            for r in chunk:
                n_seen += 1
                if r[2] == filt[2]:
                    acc += r[1]
        return peak, acc, n_seen
    raise ValueError("vm: unknown IR")


@dataclass
class Runtime:
    """Parent dispatch: check → lease → VM → publish iff G. Plain ledger."""

    ledger: PlainLedger = field(default_factory=lambda: PlainLedger(B))
    executed: bool = False
    published: Optional[int] = None
    staged: Optional[int] = None
    last_reason: str = ""
    last_lid: Optional[int] = None
    hold_lease: bool = False  # if True, do not auto-release (for concurrency tests)

    def publish(self, y: Optional[int], n_seen: int, facts: Facts) -> bool:
        if not G_csv(y, n_seen, facts):
            self.staged = None
            return False
        self.published = y
        self.staged = None
        return True

    def dispatch(self, src: str, rows: Sequence[Row], z: Proposal) -> str:
        self.executed = False
        self.published = None
        self.staged = None
        self.last_lid = None
        c = check(z, src, rows)
        if not c.ok:
            self.last_reason = c.reason
            return f"refuse:{c.reason}"
        assert c.rebuilt is not None and c.facts is not None
        lid = self.ledger.acquire(c.U)
        if lid is None:
            self.last_reason = "lease-refused"
            return "refuse:lease"
        self.last_lid = lid
        try:
            peak, value, n_seen = run_vm(c.rebuilt, rows)
            self.executed = True
            if peak > c.U:
                self.last_reason = "peak-over-lease"
                return "refuse:peak"
            self.staged = value
            if not self.publish(value, n_seen, c.facts):
                self.last_reason = "postcondition"
                return "refuse:postcondition"
            self.last_reason = "commit"
            return "commit"
        finally:
            if not self.hold_lease:
                self.ledger.release(lid)
                self.last_lid = None


def stream_U() -> int:
    facts = I_csv(ROWS)
    theta = S_csv(STREAM_SRC)
    assert theta is not None
    return U_of(L_csv(theta, facts, WINDOW, STAGED), STAGED)


def eager_U() -> int:
    return U_of(eager_load_ir(N_ROWS), STAGED)
