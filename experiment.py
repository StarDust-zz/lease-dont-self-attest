#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lease, don't self-attest.

Join of check-then-lease (Wang & Shi SkillEffect, arXiv:2608.17007) with
witness-dont-self-attest: a capacity ledger is a store. If it reverts with a
snapshot, previously-admitted live leases vanish from the restored ledger and a
second over-cap call is wrongly admitted. Self-attestation of "current charged"
follows the snapshot. An independent monotone admission witness (append-only
admit/release log that does not restore with the ledger) refuses the second
admit while the first is still live, and admits after a witnessed release. A
lagging second ledger that agrees with the snapshot gives zero gain.

Final actions: admit | refuse | hold.

Stdlib only. No network, GPU, vendor API, product import.
Run: python3 experiment.py
"""
from __future__ import annotations

from lease_core import (
    B,
    EAGER_SRC,
    N_ROWS,
    ROWS,
    STREAM_SRC,
    SUM_OK,
    WINDOW,
    Runtime,
    check,
    eager_U,
    eager_load_ir,
    make_proposal,
    run_vm,
    stream_U,
    G_csv,
    I_csv,
)
from witness_ledger import CapacityLedger, decide_heads


def check_line(i: int, name: str, ok: bool) -> None:
    print(f"{i}. {name}: {'ok' if ok else 'FAIL'}")
    if not ok:
        raise SystemExit(f"assertion {i} failed: {name}")


def scenario_1() -> None:
    """Parent check-then-lease binds: eager over-cap refused; scan publishes SUM_OK."""
    u_stream = stream_U()
    u_eager = eager_U()
    assert u_stream == 5 and u_eager == 11 and u_stream <= B < u_eager

    rt = Runtime()
    z_eager = make_proposal(
        EAGER_SRC,
        ROWS,
        window=N_ROWS,
        ir=eager_load_ir(N_ROWS),
        U=u_eager,
    )
    r1 = rt.dispatch(EAGER_SRC, ROWS, z_eager)
    ok_eager = (
        r1 == "refuse:over-cap"
        and not rt.executed
        and rt.published is None
        and u_eager > B
        and rt.ledger.charged() == 0
    )

    z_ok = make_proposal(STREAM_SRC, ROWS, WINDOW)
    c_ok = check(z_ok, STREAM_SRC, ROWS)
    peak, value, n_seen = run_vm(c_ok.rebuilt, ROWS)
    r2 = rt.dispatch(STREAM_SRC, ROWS, z_ok)
    ok_scan = (
        c_ok.ok
        and r2 == "commit"
        and rt.executed
        and rt.published == SUM_OK
        and value == SUM_OK
        and peak <= c_ok.U <= B
        and n_seen == N_ROWS
        and rt.ledger.charged() == 0
    )
    check_line(
        1,
        "parent check-then-lease binds: eager over-cap refused; scan publishes SUM_OK",
        ok_eager and ok_scan,
    )


def scenario_2() -> None:
    """Honest concurrent: first holds U; second refuse:lease; after release, commit."""
    u = stream_U()
    assert 2 * u > B and u <= B

    rt = Runtime()
    rt.hold_lease = True
    z = make_proposal(STREAM_SRC, ROWS, WINDOW)
    r1 = rt.dispatch(STREAM_SRC, ROWS, z)
    lid1 = rt.last_lid
    charged_held = rt.ledger.charged()
    # Second call while first lease still held
    r2 = rt.dispatch(STREAM_SRC, ROWS, z)
    # Release first, then second succeeds
    rt.ledger.release(lid1)
    rt.hold_lease = False
    r3 = rt.dispatch(STREAM_SRC, ROWS, z)
    ok = (
        r1 == "commit"
        and lid1 is not None
        and charged_held == u
        and charged_held + u > B
        and r2 == "refuse:lease"
        and r3 == "commit"
        and rt.published == SUM_OK
        and rt.ledger.charged() == 0
    )
    check_line(
        2,
        "honest concurrent: second refuse:lease while first held; after release, commit",
        ok,
    )


def scenario_3() -> None:
    """Snapshot-restored ledger self-attests empty → wrongly admits second over-cap."""
    u = stream_U()
    led = CapacityLedger(mode="self/snapshot", cap=B)
    snap = led.snapshot()  # empty
    a1 = led.try_admit(u)
    lid1 = led.last_lid()
    truth_live = u  # first lease still live in the world
    assert a1 == "admit" and lid1 is not None and led.charged_self() == u

    led.restore(snap)
    # Self-attestation follows the snapshot: charged looks empty.
    assert led.charged_attested() == 0 and led.charged_self() == 0
    a2 = led.try_admit(u, truth_live=truth_live)
    ok = (
        a2 == "admit"
        and led.wrong_admits == 1
        and led.charged_self() == u  # only the second; first vanished from ledger
        and truth_live + u > B
    )
    check_line(
        3,
        "snapshot self-attest empty while first live → wrong admit (wrong_admits=1)",
        ok,
    )


def scenario_4() -> None:
    """Witness log does not restore → refuse second; wrong_admits=0; first still live."""
    u = stream_U()
    results = {}
    for mode in ("witness", "self/monotone"):
        led = CapacityLedger(mode=mode, cap=B)
        snap = led.snapshot()
        a1 = led.try_admit(u)
        lid1 = led.last_lid()
        truth_live = u
        led.restore(snap)
        # Ledger bytes empty; witness / mono_live still hold the first lease.
        cw = led.charged_attested()
        a2 = led.try_admit(u, truth_live=truth_live)
        results[mode] = {
            "a1": a1,
            "a2": a2,
            "wrong": led.wrong_admits,
            "cw": cw,
            "lid1": lid1,
            "live": led.live_lids_witness() if mode == "witness" else list(led.mono_live),
        }
    ok = all(
        r["a1"] == "admit"
        and r["a2"] == "refuse"
        and r["wrong"] == 0
        and r["cw"] == u
        and r["cw"] + u > B
        and r["lid1"] in r["live"]
        for r in results.values()
    )
    check_line(
        4,
        "witness/monotone: refuse second while first live (wrong_admits=0; charged+U>B)",
        ok,
    )


def scenario_5() -> None:
    """Lagging replica restored with the same snapshot → same wrong admit; zero gain."""
    u = stream_U()
    snap_led = CapacityLedger(mode="self/snapshot", cap=B)
    lag_led = CapacityLedger(mode="cross/lagging", cap=B)
    snap = snap_led.snapshot()
    # Keep lag twin in sync with the same empty snapshot baseline.
    lag_led.restore(lag_led.snapshot())

    a1s = snap_led.try_admit(u)
    a1l = lag_led.try_admit(u)
    truth_live = u
    snap_led.restore(snap)
    lag_led.restore(snap)  # same restore → lag agrees with snapshot

    assert snap_led.charged_attested() == 0
    assert lag_led.charged_attested() == 0
    assert lag_led.charged_lag() == 0

    a2s = snap_led.try_admit(u, truth_live=truth_live)
    a2l = lag_led.try_admit(u, truth_live=truth_live)
    ok = (
        a1s == a1l == "admit"
        and a2s == a2l == "admit"
        and snap_led.wrong_admits == 1
        and lag_led.wrong_admits == 1
        and snap_led.wrong_admits == lag_led.wrong_admits  # zero gain
    )
    check_line(
        5,
        "lagging replica agrees with snapshot → same wrong admit; zero gain",
        ok,
    )


def scenario_6() -> None:
    """After witnessed release, witness admits next legal call; failing G does not publish."""
    u = stream_U()
    led = CapacityLedger(mode="witness", cap=B)
    snap = led.snapshot()
    a1 = led.try_admit(u)
    lid1 = led.last_lid()
    led.restore(snap)
    a2_blocked = led.try_admit(u, truth_live=u)
    # Witnessed release of the first (still-live) lease.
    led.release(lid1)
    assert led.charged_witness() == 0
    a3 = led.try_admit(u)
    lid3 = led.last_lid()

    # Publish still requires postcondition G (parent contract).
    facts = I_csv(ROWS)
    z = make_proposal(STREAM_SRC, ROWS, WINDOW)
    c = check(z, STREAM_SRC, ROWS)
    peak, value, n_seen = run_vm(c.rebuilt, ROWS)
    fail_g = G_csv(-1, n_seen, facts)
    ok_g = G_csv(value, n_seen, facts)
    rt = Runtime()
    # Stage then try failing publish
    rt.staged = value
    published_fail = rt.publish(-1, n_seen, facts)
    published_ok = rt.publish(value, n_seen, facts)

    ok = (
        a1 == "admit"
        and a2_blocked == "refuse"
        and a3 == "admit"
        and lid3 is not None
        and led.wrong_admits == 0
        and fail_g is False
        and ok_g is True
        and published_fail is False
        and published_ok is True
        and rt.published == SUM_OK
        and peak <= u <= B
    )
    check_line(
        6,
        "after witnessed release, admit next legal; failing G does not publish",
        ok,
    )


def scenario_7() -> None:
    """Refuse vs hold: backward step → refuse; pure lag → hold; caught up → admit."""
    # Backward step: attested admission head went from 7 → 3
    v_back = decide_heads(local_head=3, attested_head=7, seen_head=7)
    # Pure lag: head stuck at 3, never moved back (seen was 0 or 3), attested is 7
    v_lag = decide_heads(local_head=3, attested_head=7, seen_head=3)
    v_lag0 = decide_heads(local_head=3, attested_head=7, seen_head=0)
    # Caught up: no backward step, head matches attested
    v_ok = decide_heads(local_head=7, attested_head=7, seen_head=7)
    # Also: caught up after lag (local advanced to attested)
    v_catch = decide_heads(local_head=7, attested_head=7, seen_head=3)

    ok = (
        v_back == "refuse"
        and v_lag == "hold"
        and v_lag0 == "hold"
        and v_ok == "admit"
        and v_catch == "admit"
    )
    check_line(
        7,
        "refuse on backward head 7→3; hold on pure lag; admit when caught up",
        ok,
    )


def main() -> None:
    scenario_1()
    scenario_2()
    scenario_3()
    scenario_4()
    scenario_5()
    scenario_6()
    scenario_7()
    print("7/7 assertions passed")


if __name__ == "__main__":
    main()
