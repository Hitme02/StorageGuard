"""Physical execution: a disposable, file-backed storage sandbox where deletions are real.

The twin (``storageguard.twin``) is the control plane: it decides what an operation
does and what the agent sees.  This package adds a *data plane* made of real files:

* every volume, snapshot, clone, replica and backup is an image file of
  checksummed commits (``image``) inside a sandbox directory (``sandbox``);
* every successful mutation is executed on those files by a driver that reads only
  the public API (``executor``): ``delete_volume`` really removes the volume's
  directory with its snapshots and stored backups, ``restore_snapshot`` really
  overwrites the image, a snapshot lock really sets the OS immutable flag;
* data loss is measured by reading what is physically left (``measure``):
  checksum-verified recovery of every dataset, then two standard drills on copies
  of the sandbox (the primary site is destroyed; ransomware with admin rights).

``world`` creates the initial files from the scenario's ground truth.  It is the only
module here that reads hidden twin fields; the driver and the measurement never do,
and nothing in this package imports the guard, the graph or the harm oracle.  The
sandbox refuses to touch any path outside its own marked directory.
"""
