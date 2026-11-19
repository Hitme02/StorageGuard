#!/bin/sh
# Dev-split runs: every config end to end on 8 dev trap tasks, then the full dev split for B0 and B4.
set -x
venv/bin/sg run dev --categories trap --limit 8
venv/bin/sg run dev --configs "noguard;storageguard"
