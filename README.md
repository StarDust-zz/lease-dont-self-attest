# Lease, don't self-attest

Synergy deep-dive joining
[check-then-lease](https://github.com/StarDust-zz/check-then-lease)
(Wang & Shi SkillEffect, arXiv:2608.17007: tool program is a proposal;
independent checker; atomic capacity lease; staged publish iff G) with
[witness-dont-self-attest](https://github.com/StarDust-zz/witness-dont-self-attest)
(a store cannot vouch for itself across its own rollback; only a version
record that never goes backwards can attest).

Keeper: a capacity ledger is a store. Snapshot-restore makes previously
admitted live leases vanish from the ledger; self-attestation of "current
charged" follows the snapshot and wrongly admits a second over-cap call
(`wrong_admits=1`). An append-only admission witness that does not restore
with the ledger refuses the second admit (`wrong_admits=0`). A lagging
second ledger restored with the same snapshot agrees with it: zero gain.
Refuse on a backward head step; hold on pure lag; admit when caught up.

## Run

```bash
python3 experiment.py
```

Last run: **7/7 assertions passed**. Python 3 stdlib only. No network, GPU,
vendor API, or product import.

## Layout

| file | what it is |
| --- | --- |
| [`lease_core.py`](lease_core.py) | minimal check-then-lease algebra (rows, check, U, B=6, eager vs scan) |
| [`witness_ledger.py`](witness_ledger.py) | capacity ledger modes: self/snapshot, self/monotone, cross/lagging, witness |
| [`experiment.py`](experiment.py) | seven assertions |
| [`DESCRIPTION.md`](DESCRIPTION.md) | claim, falsifier, object, synergy, what 7/7 showed |
| [`KEEPERS.txt`](KEEPERS.txt) | plain-language keepers vs still-hunch |

## Parents

- https://github.com/StarDust-zz/check-then-lease
- https://github.com/StarDust-zz/witness-dont-self-attest

## License

MIT. See [`LICENSE`](LICENSE). Copyright (c) 2026 StarDust-zz.
