# Workspace naming and migration

VlogSync is the product and repository name. A local installation may keep an older workspace folder name. Set `WEEKLY_VLOG_MANAGER_ROOT` to the exact folder to preserve compatibility with older releases. A fresh setup can use `VlogSync`.

Before renaming a live workspace, stop the controller, verify the CapCut native queue is empty, inventory absolute paths and file-provider IDs, and capture the existing marker, numbering ledger, plans, receipts and cross-device mappings. Check every launcher, rollback binary and archived helper for a hardcoded old folder path. Confirm the new mount path against live filesystem metadata and provider ID where applicable. Test a read-only preview and numbering lookup on the candidate and on the rollback. Delay relocation if either check fails. Do not duplicate the workspace to imitate a shared live number ledger.

Treat stale paths in older receipts as historical evidence. Rebind in a separate checkpoint or reconciliation record while preserving the originals. Never rely on rename as evidence that old local or cloud receipts still establish the current destination.

A branded release and documentation folder can coexist with a legacy active workspace. Keep a single writable ledger and bind the launcher to the existing workspace with a local environment override. Keep archived installs and their original packages outside the public repository. Before rollback, close any active controller, preserve its journals, and check the older release against the same active ledger. Review any pending jobs before rendering.

## Preserve both completion migrations

A migrated archive can carry two private bindings in `%LOCALAPPDATA%/VlogSync`. `completion-migration-v1.json` seals the original production plan and server-verified publication receipts. `drive-organization-v1.json` binds those and earlier verified pairs to their permanent Drive paths without rewriting their historical receipts. Back up both bindings with the original plans, receipts and single numbering ledger. Keep private bindings and object IDs outside Git.

Before qualifying a future release, run `python -B -m unittest test_completion_migrations` alongside the normal suite. Test a clean workspace and a representative migrated workspace. Reconcile saved identities to all existing pairs, confirm the next ledger number, and verify that a missing or changed destination holds before any submission. Never reset the archive ledger or use test defaults on a migrated workspace. Keep the previous working binary and its private migration bundle available for rollback.

## Phase 2 installation and rollback

Install 1.2.0 alongside the previous release. To roll back, close the panel after its active work has stopped, then use the retained 1.1.0 launcher against the same workspace. Preserve its ledger, migration records and any unfinished journals. Its local panel has a maintenance hold, an explicit qualification gate, process serialization, and a separate fixture ledger. Preserve private chronological rebinding and both historical migration bindings separately. The camera watcher runs only while the optional local launcher is open and reacts to connection by opening the panel. It never submits imports. On a new setup confirm folder configuration, device recognition, numbering, and one disposable native run before enabling live mode.

For 1.2.1 rollback, close the idle panel, restore the saved 1.2.0 qualification marker, and use the retained 1.2.0 launcher. Keep the canonical ledger and both completion bindings in place.
