# Workspace naming and migration

VlogSync is the product and repository name. A local installation may keep an older workspace folder name. Set `WEEKLY_VLOG_MANAGER_ROOT` to the exact folder to preserve compatibility with older releases. A fresh setup can use `VlogSync`.

Before renaming a live workspace, stop the controller, verify the CapCut native queue is empty, inventory absolute paths and file-provider IDs, and capture the existing marker, numbering ledger, plans, receipts and cross-device mappings. Check every launcher, rollback binary and archived helper for a hardcoded old folder path. Confirm the new mount path against live filesystem metadata and provider ID where applicable. Test a read-only preview and numbering lookup on the candidate and on the rollback. Delay relocation if either check fails. Do not duplicate the workspace to imitate a shared live number ledger.

Treat stale paths in older receipts as historical evidence. Rebind in a separate checkpoint or reconciliation record while preserving the originals. Never rely on rename as evidence that old local or cloud receipts still establish the current destination.

A branded release and documentation folder can coexist with a legacy active workspace. Keep a single writable ledger and bind the launcher to the existing workspace with a local environment override. Keep archived installs and their original packages outside the public repository. Before rollback, close any active controller, preserve its journals, and check the older release against the same active ledger. Review any pending jobs before rendering.
