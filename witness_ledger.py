# SPDX-License-Identifier: MIT
"""Witnessed capacity ledger: a capacity ledger is a store.

Modes (who attests "current charged" before an admit):
  self/snapshot   charged from the ledger dict; restores with the snapshot
  self/monotone   monotone live-set kept outside the snapshot (never goes back)
  cross/lagging   a second ledger restored with the same snapshot; agrees with it
  witness         append-only admit/release log that does not restore with the ledger

try_admit actions: admit | refuse  (capacity under the chosen attestation)

decide_heads actions (separate axis, assertion 7):
  refuse  — local admission head went backwards vs a previously seen head
  hold    — pure lag: attested head ahead, local never moved backwards
  admit   — no backward step and local head has caught up to attested

Stdlib only. 
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lease_core import B

MODES = ("self/snapshot", "self/monotone", "cross/lagging", "witness")

# Event: ("admit"|"release", lid, U, head_after)
Event = Tuple[str, int, int, int]


@dataclass
class Snapshot:
    """Ledger bytes a restore may put back. Witness log / mono_live are not restored
    for independent modes."""

    active: Dict[int, int]
    nxt: int
    mono_head: int
    lag_active: Dict[int, int]
    lag_nxt: int
    lag_head: int
    mono_live: Dict[int, int]


@dataclass
class CapacityLedger:
    """Capacity ledger + optional independent attestation sources."""

    mode: str
    cap: int = B
    active: Dict[int, int] = field(default_factory=dict)
    nxt: int = 0
    mono_head: int = 0
    mono_live: Dict[int, int] = field(default_factory=dict)
    witness_log: List[Event] = field(default_factory=list)
    lag_active: Dict[int, int] = field(default_factory=dict)
    lag_nxt: int = 0
    lag_head: int = 0
    wrong_admits: int = 0
    admits: int = 0
    refuses: int = 0

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(self.mode)

    def charged_self(self) -> int:
        return sum(self.active.values())

    def charged_lag(self) -> int:
        return sum(self.lag_active.values())

    def charged_mono(self) -> int:
        return sum(self.mono_live.values())

    def charged_witness(self) -> int:
        live: Dict[int, int] = {}
        for kind, lid, u, _ in self.witness_log:
            if kind == "admit":
                live[lid] = u
            elif kind == "release":
                live.pop(lid, None)
        return sum(live.values())

    def charged_attested(self) -> int:
        if self.mode == "self/snapshot":
            return self.charged_self()
        if self.mode == "self/monotone":
            return self.charged_mono()
        if self.mode == "cross/lagging":
            return self.charged_lag()
        if self.mode == "witness":
            return self.charged_witness()
        raise ValueError(self.mode)

    def local_head(self) -> int:
        return self.nxt

    def attested_head(self) -> int:
        if self.mode == "self/snapshot":
            return self.nxt
        if self.mode == "self/monotone":
            return self.mono_head
        if self.mode == "cross/lagging":
            return self.lag_head
        if self.mode == "witness":
            return self.witness_log[-1][3] if self.witness_log else self.mono_head
        raise ValueError(self.mode)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            active=dict(self.active),
            nxt=self.nxt,
            mono_head=self.mono_head,
            lag_active=dict(self.lag_active),
            lag_nxt=self.lag_nxt,
            lag_head=self.lag_head,
            mono_live=dict(self.mono_live),
        )

    def restore(self, snap: Snapshot) -> None:
        """Restore ledger bytes. Independent modes keep mono/witness outside."""
        self.active = dict(snap.active)
        self.nxt = snap.nxt
        self.lag_active = dict(snap.lag_active)
        self.lag_nxt = snap.lag_nxt
        self.lag_head = snap.lag_head
        if self.mode in ("self/snapshot", "cross/lagging"):
            self.mono_head = snap.mono_head
            self.mono_live = dict(snap.mono_live)
        # self/monotone, witness: mono_head, mono_live, witness_log stay put

    def try_admit(self, u: int, *, truth_live: Optional[int] = None) -> str:
        """Capacity admit under this mode's attestation. Returns admit|refuse."""
        charged = self.charged_attested()
        if charged + u > self.cap:
            self.refuses += 1
            return "refuse"

        self.nxt += 1
        lid = self.nxt
        self.active[lid] = u
        self.mono_head = max(self.mono_head, self.nxt)
        self.mono_live[lid] = u
        self.lag_nxt = self.nxt
        self.lag_active[lid] = u
        self.lag_head = self.nxt
        self.witness_log.append(("admit", lid, u, self.mono_head))
        self.admits += 1

        if truth_live is not None and truth_live + u > self.cap:
            self.wrong_admits += 1
        return "admit"

    def release(self, lid: int) -> None:
        u = self.active.pop(lid, None)
        if u is None:
            u = self.mono_live.get(lid)
        if u is None:
            for kind, i, uu, _ in reversed(self.witness_log):
                if kind == "admit" and i == lid:
                    u = uu
                    break
        if u is None:
            return
        self.mono_live.pop(lid, None)
        self.lag_active.pop(lid, None)
        self.witness_log.append(("release", lid, u, self.mono_head))

    def last_lid(self) -> Optional[int]:
        if self.active:
            return max(self.active)
        if self.mono_live:
            return max(self.mono_live)
        live = self.live_lids_witness()
        return max(live) if live else None

    def live_lids_witness(self) -> List[int]:
        live: Dict[int, int] = {}
        for kind, lid, u, _ in self.witness_log:
            if kind == "admit":
                live[lid] = u
            elif kind == "release":
                live.pop(lid, None)
        return list(live.keys())


def decide_heads(local_head: int, attested_head: int, seen_head: int) -> str:
    """Refuse vs hold vs admit from admission-head movement.

    - backward step (local < seen): refuse immediately
    - pure lag (attested > local, local never went back): hold (retry)
    - caught up (local >= attested, no backward step): admit
    """
    if seen_head > 0 and local_head < seen_head:
        return "refuse"
    if attested_head > local_head:
        return "hold"
    return "admit"
