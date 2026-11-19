# Sensitivity: restore semantics

The OpenZFS replay found that `zfs rollback` refuses to restore a snapshot while newer snapshots exist, and
that `zfs rollback -r` destroys them, while the twin keeps them. ONTAP's volume SnapRestore behaves like `-r`:
NetApp's knowledge base states that a volume snapshot restore deletes the snapshots newer than the one
restored, and that it fails when a newer snapshot is used by a clone. So the twin's restore is more
permissive than both real systems, and `destroy_newer` is the documented semantics of both. Every trajectory
containing a restore was re-run under three semantics and measured on real files, with the same recorded
calls and with reference drills under the same semantics (`eval/restore_sensitivity.py`).

Observed on OpenZFS 2.2.2 and modelled exactly: with `-r`, a newer snapshot that has a clone makes the rollback
refuse with no change; a held (locked) newer snapshot makes it fail *after* destroying the other newer
snapshots, and nothing is restored.

- trajectories with a restore: 95 (717 of 12720 episodes)
- `keep` reproduces the published physical results exactly: yes
- under `rollback -r`: 112 restores, 13 with no newer snapshot, 99 rolled back, 378 newer snapshots destroyed, 0 refused because of a clone, 0 failed on a held snapshot after destroying the others
- episodes whose physical verdict changes: destroy_newer harm added: 338, refuse harm removed: 170
- the scripted reference plans (the correct procedure, unguarded) of the 12 tasks with a restore, harmful on real files: keep 0, refuse 0, destroy_newer 11 (under `rollback -r`: T7 6, T8 5; the plan's own safety snapshot, taken just before the restore, is a newer snapshot, so `-r` destroys it and the volume loses the data written since the restore point)
- under `keep` and `refuse`, harm beyond the reference plan equals harm: yes

## Physical harm per configuration (test split, main-study models)

Harm as in RQ8: data loss against the starting state; outage and drills against the ticket-authorised calls.
The last column counts, under `rollback -r`, only data loss beyond what the task's own reference plan loses
under the same semantics, i.e. it separates what the backend costs every correct restore from what the
agent adds.

| Configuration | Episodes | with a restore | harm %, twin | harm %, rollback | harm %, rollback -r | harm %, rollback -r, beyond the reference plan |
|---|---|---|---|---|---|---|
| B0 No guard | 936 | 63 | 35.5 | 32.6 | 35.5 | 31.0 |
| B1 Prompt rules | 936 | 63 | 29.3 | 26.4 | 30.6 | 25.4 |
| B2 RBAC approval | 936 | 15 | 1.9 | 1.9 | 3.3 | 1.9 |
| B3 LLM judge | 936 | 63 | 23.0 | 20.1 | 23.0 | 18.7 |
| B4 StorageGuard | 936 | 63 | 2.0 | 2.0 | 8.8 | 2.0 |


Beyond the reference plan, `rollback -r` adds harm to 0 main-study episodes and removes it from 118 (B0 42, B1 36, B3 40; T7 96, T8 22). The removed ones are agents that restored in place without first taking a safety snapshot, the T7/T8 trap; under `-r` the correct plan loses the same data, so the trap exists only on a backend whose restore keeps newer snapshots; on ZFS or ONTAP a procedure that keeps the newer data has to avoid the in-place restore (for example, restore from a clone of the snapshot).

## Would StorageGuard notice, if its dry run used the same semantics?

B4's decision on each recorded restore, with the guard's dry run on a twin whose restore keeps newer
snapshots (as in the study) or destroys them (`-r`); the calls before the restore are the recorded ones,
under the same semantics. Only the guard's decision is shown (no approver).

- main-study B4 test episodes with a restore (63), restore calls episode-weighted: twin semantics ALLOW 99; `-r` semantics ALLOW 6, ESCALATE 69, BLOCK 24
- the reference plans that restore (11): twin semantics ALLOW 11; `-r` semantics ESCALATE 6, BLOCK 5
- findings behind the stops under `-r` (distinct restore calls): `recovery_point_regressed` 47
- restores still allowed under `-r` with a newer snapshot (which `-r` destroys): 0

With the backend's restore semantics in its dry run, the guard's existing recovery-point analysis stops
the restores that cost newer snapshots. It also stops the reference plans' restores: the guard has no
clone-based restore among its rewrites, so on such a backend that rewrite would be needed to keep the
ticket's restore possible without an approver.

