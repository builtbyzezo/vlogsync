# VlogSync

## Public introduction

VlogSync is a Windows controller for turning reviewed camera recordings into numbered 1080p HEVC masters and matched 720p H.264 editing proxies. CapCut performs the native renders. VlogSync prepares drafts, manages a bounded export queue, verifies pairs, and publishes byte-checked copies to ordinary filesystem folders.

```text
Camera and reviewed clips
        ↓
Frozen grouping, order, orientation and reserved vlog number
        ↓
CapCut draft → native master queue → native proxy queue
        ↓
Pair timing and content checks → verified destination copies
```

The Phase 1 production baseline completed a supervised workflow on Windows with the Insta360 Ace Pro 2 and CapCut 9.8.0.4213. The public code is a release candidate. Fully unattended recovery, broader camera formats, a standalone installer, and the planned HTML operation panel remain separate qualification or development work. Other cameras and CapCut releases need testing.

### Installation and first run

1. Use 64-bit Windows with an active desktop session, English Windows OCR, Python 3.12, CapCut 9.8.0.4213 in English, and a compatible Process Vlogs 3.7.1 installation. The public package does not distribute Process Vlogs, CapCut, the Windows OCR engine, or FFmpeg. The current source is usable as an integration checkout. A standalone install requires extracting or replacing the Process Vlogs adapter contract.
2. Download the tagged source ZIP or clone the repository. Keep the three source directories at their relative locations. Install the pinned Python package with `python -m pip install -r "Weekly Vlog Export/requirements.txt"`.
3. Supply a local VlogSync workspace and use `WEEKLY_VLOG_MANAGER_ROOT` to point to it. Use a JSON marker at `Configuration/controller.json` with a `workspace` value starting with `weekly-vlog-export-`, an `extra_footage` sibling directory name, and optional `filesystem_exports` role mappings relative to the workspace parent. Create those directories and initialize `Configuration/numbering.json` with schema 1, an integer `observed_through`, a `last_assigned` object containing `number` and `recording_date`, and an empty `assignments` array for a genuinely fresh install. Migration preserves the existing ledger instead.
4. Copy the private reviewed-plan and evidence records needed by your setup to `Records`. The current controller expects compatible Process Vlogs state, valid CapCut templates `1122` and `1123`, and reviewed date-to-source membership. The legacy reconciliation adapters require their original audit and publication records. The public ZIP contains no private example records.
5. Run the automated suite from `Weekly Vlog Export` with `python -B -m unittest -v test_phase1`, then qualify disposable clips on the actual display and device. Inspect `--numbering` and `--preview`. Only after approving that preview use `--reserve-reviewed` and then `--execute-preview`. Keep the frozen plan for recovery.

The CapCut window must be visible and on an unlocked desktop. The current automation assumes qualified layouts and requires explicit review for ambiguous timestamps, VFR or HDR profiles, source changes, and unsupported companions. The camera is always read-only.

[Source and downloads](https://github.com/builtbyzezo/vlogsync) · [Tagged releases](https://github.com/builtbyzezo/vlogsync/releases)

## Technical documentation and AI handover

| Component | Contract |
|---|---|
| `weekly_vlog_export.py` | Discovery, identity reconciliation, immutable reviewed plans, numbered scheduling, native queue and journals |
| `create_capcut_draft.py` | Source metadata contracts, project templates, display rotation, direct references and readback verification |
| `CapCut Test 3/run_capcut_test3.py` | Native trigger adapter, export paths and timing readback |
| `capcut_ui.py`, `desktop_controls.py`, `windows_ocr.ps1` | Process and window identity, guarded UI interaction and local OCR |
| `numbering.py`, `deployment.py`, `extra_footage.py` | Persistent ordered-identity ledger, workspace discovery and optional intake |
| `filesystem_delivery.py`, `wve_safety.py` | Resume-safe staging, checksum and collision checks, snapshots, clearance and power guards |
| `content_probe.py`, `retained_audit.py` | Independent picture and audio sampling and retained-archive follow-up |
| `phase1_control.py`, `qualify_native_rc1.py`, `test_phase1.py` | Operator actions, disposable native workflow and automated unit/media cases |

### Processing and recovery

The local review is authoritative for saved order and overnight grouping. A new preview rediscovers the camera path and checks source identity. Number reservations bind a date and ordered identity hash. Approval freezes the plan. Drafts reference source files directly, then master and proxy rounds submit up to five native jobs. Only the original submission journal and recognized CapCut state can establish an accepted job. Verification checks codec, dimensions, frame timing, expected audio and sampled content. The active journal stays local. Dated recovery snapshots contain private paths and belong only in the configured workspace.

After a crash, inspect the same plan and queued or completed jobs. A lost native render may require restarting from its beginning. Never resubmit uncertain work. Preserve any conflicting file. A verified failed job can receive one explicit retry after CapCut closes and partial files are reconciled. See `phase1_control.py --help`. Power actions require explicit opt-in and a successful saved terminal state with verified publication. Keep them off until supervised qualification.

### Configuration and migration

Preserve the workspace marker, number ledger, reviewed source order, private delivery receipts, exact approved CapCut build and template drafts. Rebind drafts to the attached source paths before retrying on another device. Preserve the exact configured output roots and recorded filesystem identities. The controller's integration imports the installed review and media utilities, including `vlog_core`, `weekly_camera`, `editor_proxy`, and legacy cloud receipt helpers. Treat version bumps there as adapter changes. New publication uses ordinary filesystem destinations and does not wait for cloud synchronization.

### Qualification and troubleshooting

Use synthetic fixtures for crash and cancellation tests. Confirm approved queue order, all output metadata, no duplicate triggers, unchanged prior outputs and preset restoration. Test scale, lock/unlock, removable storage, and lost destinations under supervision. A format-clearing decision also requires reconciling non-video camera assets. Sampled comparisons are evidence for the windows checked.

On a failure, preserve the plan, queue, partial outputs and job receipts. Resolve out-of-focus windows and project or output ambiguity before resuming. The planned **Copy Troubleshooting Prompt for ChatGPT** action uses a sanitized diagnostic record. Keep raw logs and any current paths private.

### Release process and design decisions

Single-laptop scheduling and native rendering avoid another encoder and unnecessary media copies. Fail-closed guards are intentional where the UI or a native submission cannot be identified. Update the documentation, changelog and status, run tests in a clean checkout, and audit tracked files, history and ZIP entries before creating a tag. Use a release-candidate tag until qualification is complete. Publish only allowlisted source, docs, synthetic fixture generators, license notices and SHA-256 with the archive. Preserve the previous release independently.

[Development instructions](AGENTS.md) · [Status](CURRENT_STATUS.md) · [Changes](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [Migration](MIGRATION.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)
