# Lease, don't self-attest

Synergy deep-dive joining two published packs (not a new arXiv scout):

- [check-then-lease](https://github.com/StarDust-zz/check-then-lease)
  — Wang & Shi, *SkillEffect*, [arXiv:2608.17007](https://arxiv.org/abs/2608.17007).
  A skill's tool program is only a proposal. An independent checker rebuilds
  the bounded IR from program + immutable input; an atomic capacity lease
  admits the checked peak; staged publication waits on postcondition G.
- [witness-dont-self-attest](https://github.com/StarDust-zz/witness-dont-self-attest)
  — join of recheck-after-forget × refuse-to-certify. A store cannot vouch
  for itself across its own rollback; only a version record that never goes
  backwards (external monotone counter / independent replica / append-only
  witness) can attest. Self-attestation and a lagging second home add nothing.

## Claim

A capacity ledger is a store. If it reverts with a snapshot, previously
admitted live leases vanish from the restored ledger and a second over-cap
call is wrongly admitted. Self-attestation of "current charged" from the
restored ledger follows the snapshot. An independent monotone admission
witness (append-only log of admit/release events that does not restore with
the ledger) refuses the second admit while the first is still live, and
admits after a witnessed release. A lagging second ledger that agrees with
the snapshot gives zero gain.

Final actions: **admit | refuse | hold**.

## Falsifier

If a snapshot-restored ledger refused the second over-cap while the first
lease was still live; if the witness admitted it (`wrong_admits > 0`); if a
lagging replica restored with the same snapshot caught the wrong admit when
self/snapshot did not (or vice versa — any gain); if a backward head step
held instead of refusing; if a pure lag refused instead of holding; if
failing G still published.

## Object

```
rows / B=6 / U_stream=5 / U_eager=11 / SUM_OK=18 / window=2   (from check-then-lease)

capacity ledger modes (who attests "current charged"):
  self/snapshot   charged from ledger dict; restores with the snapshot
  self/monotone   monotone live-set kept outside the snapshot
  cross/lagging   second ledger restored with the same snapshot (agrees)
  witness         append-only admit/release log that does not restore

try_admit:  admit iff charged_attested + U ≤ B; else refuse
decide_heads(local, attested, seen):
  local < seen          → refuse   (backward step)
  attested > local      → hold     (pure lag, retry)
  else                  → admit    (caught up)

truth_live: ground-truth charged from leases still live in the world
            (scores wrong_admits after a snapshot restore)
```

## What 7/7 showed

1. Parent check-then-lease binds: eager whole-input over-cap refused before
   execute; bounded scan commits and publishes SUM_OK (=18).
2. Honest concurrent: first holds U=5 (room left 1, not enough for a second
   5); second `refuse:lease` while first held; after release, second commits.
3. Snapshot-restored self/snapshot ledger attests charged=0 while first lease
   still live → wrongly admits second over-cap (`wrong_admits=1`). Inject /
   failure mode.
4. Same scenario with witness or self/monotone: refuse second
   (`wrong_admits=0`); first still live on the witness; `charged_witness + U > B`.
5. cross/lagging restored with the same snapshot agrees → same wrong admit
   as (3); zero gain vs self/snapshot.
6. After witnessed release of the first lease, witness admits the next legal
   call; publish still requires G (failing G does not publish).
7. Refuse vs hold: head 7→3 → refuse; pure lag (head stuck) → hold; caught
   up → admit.

## Synergy

check-then-lease, in plain words: don't trust the skill program — rebuild the
bounded call, lease the peak, publish only if G holds. witness-dont-self-attest,
in plain words: a store that rolled back cannot certify that its own counter is
newest; you need a record that did not roll back with it.

Joined: the capacity ledger *is* that store. Leasing against a restored
ledger is self-attestation. The admission witness is the independent record.

## Caveat

One CSV-shaped streaming reduction and one capacity number (B=6, U=5). Not
SkillEffect's six plugins; not the 88-case recheck matrix. The discrete
contrast (wrong admits with snapshot vs 0 with witness) is what held.
