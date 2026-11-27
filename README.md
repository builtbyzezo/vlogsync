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

VlogSync 1.2.3 adds a local browser panel for reviewing imports, approving exports, tracking progress and opening destination folders. The Phase 1 production baseline completed a supervised workflow on Windows with the Insta360 Ace Pro 2 and CapCut 9.8.0.4213. Version 1.1.0 is the stable Phase 1 release for the qualified laptop. Clean extraction, one unattended native crash recovery, a complete export cycle at 100% scaling, and supervised Windows sleep and shutdown were verified there. Second-computer compatibility is untested. Ambiguous multi-job recovery requires manual review. Broader camera support, editor uploads and publishing automation are planned.

### Installation and first run

1. Use 64-bit Windows with an active desktop session, English Windows OCR, Python 3.12, CapCut 9.8.0.4213 in English, and a compatible FFmpeg/FFprobe installation. VlogSync no longer imports Process Vlogs. Install CapCut, Windows OCR and FFmpeg separately. Set VLOGSYNC_FFMPEG_BIN to the directory containing both tools if they are outside PATH.
2. Download the [1.3.0 installation ZIP](https://github.com/builtbyzezo/vlogsync/archive/refs/tags/v1.3.0.zip) or clone the repository. Keep the three source directories at their relative locations. Install the pinned Python package with `python -m pip install -r "Weekly Vlog Export/requirements.txt"`.
3. From the `Weekly Vlog Export` folder, run `python setup_vlogsync.py "C:/Vlog Workspace/VlogSync" --last-number 0 --timezone Etc/UTC` for a fresh archive, using the recording timezone in place of Etc/UTC. Set the printed PowerShell workspace override. Migrating operators must reuse their existing ledger and reviewed records instead of resetting the next number.
4. Create local CapCut drafts named `1122` (one synthetic clip with audio at 29.97 fps) and `1123` (empty) in the qualified CapCut version, then save and close them normally. VlogSync verifies the template layout before registering clones. Preserve or import the private reviewed source groups into `%LOCALAPPDATA%/VlogSync/review-session.json` if migrating. A new workspace can discover footage with no prior review. Inspect the preview explicitly before reserving numbers.
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
| `camera_media.py`, `setup_vlogsync.py`, `crash_recovery.py` | Independent media discovery and provisioning, plus a single guarded crash retry |
| `numbering.py`, `deployment.py`, `extra_footage.py` | Persistent ordered-identity ledger, workspace discovery and optional intake |
| `filesystem_delivery.py`, `wve_safety.py` | Resume-safe staging, checksum and collision checks, snapshots, clearance and power guards |
| `content_probe.py`, `retained_audit.py` | Independent picture and audio sampling and retained-archive follow-up |
| `phase1_control.py`, `qualify_native_rc1.py`, `test_phase1.py` | Operator actions, disposable native workflow and automated unit/media cases |

### Processing and recovery

The local review is authoritative for saved order and overnight grouping. A new preview rediscovers the camera path and checks source identity. Number reservations bind a date and ordered identity hash. Approval freezes the plan. Drafts reference source files directly, then master and proxy rounds submit up to five native jobs. Only the original submission journal and recognized CapCut state can establish an accepted job. Verification checks codec, dimensions, frame timing, expected audio and sampled content. The active journal stays local. Dated recovery snapshots contain private paths and belong only in the configured workspace.

After a crash, inspect the same plan and queued or completed jobs. A lost native render may require restarting from its beginning. Never resubmit uncertain work. Preserve any conflicting file. A verified failed job can receive one explicit retry after CapCut closes and partial files are reconciled. See `phase1_control.py --help`. Power actions require explicit opt-in and a successful saved terminal state with verified publication. Sleep and shutdown were qualified under supervision, with Windows event-log confirmation after wake and reboot. Review any ambiguous multi-job state manually before resubmitting.

### Configuration and migration

Preserve the workspace marker, number ledger, reviewed source order, private delivery receipts, exact approved CapCut build and template drafts. Rebind drafts to the attached source paths before retrying on another device. Preserve the exact configured output roots and recorded filesystem identities. The controller owns its camera-media adapter and preserves the previous sampled SHA-256 scheme. Legacy historical receipts are held for reconciliation unless the original Keep proof or a native VlogSync receipt qualifies. Existing Process Vlogs activity is detected as an optional safety interlock if it happens to be installed. New publication uses ordinary filesystem destinations and does not wait for cloud synchronization.

### Qualification and troubleshooting

Use synthetic fixtures for crash and cancellation tests. Confirm approved queue order, all output metadata, no duplicate triggers, unchanged prior outputs and preset restoration. Test scale, lock/unlock, removable storage, and lost destinations under supervision. A format-clearing decision also requires reconciling non-video camera assets. Sampled comparisons are evidence for the windows checked.

On a failure, preserve the plan, queue, partial outputs and job receipts. Resolve out-of-focus windows and project or output ambiguity before resuming. The planned **Copy Troubleshooting Prompt for ChatGPT** action uses a sanitized diagnostic record. Keep raw logs and any current paths private.

### Release process and design decisions

Single-laptop scheduling and native rendering avoid another encoder and unnecessary media copies. Fail-closed guards are intentional where the UI or a native submission cannot be identified. Update the documentation, changelog and status, run tests in a clean checkout, and audit tracked files, history and ZIP entries before creating a tag. Use a release-candidate tag until qualification is complete. Publish only allowlisted source, docs, synthetic fixture generators, license notices and SHA-256 with the archive. Preserve the previous release independently.

[Development instructions](AGENTS.md) · [Status](CURRENT_STATUS.md) · [Changes](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [Migration](MIGRATION.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)

## Local operator panel

Double-click `Open VlogSync Candidate.cmd` after extracting the complete package on Windows with Python 3.12. The local panel opens in the default browser with synthetic recordings and an isolated numbering ledger under `interface/Runs`. Import previews only scan the fixture in this mode. Approve explicitly to observe the simulated queue. Use the window that launched the server to stop it when finished.

The panel uses the existing controller for real preview, reserved numbering, CapCut queue controls, verification and filesystem publication. The supported Windows laptop passed a disposable native export cycle. Use `Open VlogSync.cmd` for an approved workspace. For another installation, qualify a disposable workspace first, then create `Configuration/phase2-qualification.json` containing `{"approved_version":"1.3.0"}` in the live workspace before launching. The existing CLI and the 1.1.0 package remain available for rollback. Do not run both controllers concurrently. Google Drive for desktop owns synchronization. The panel has no Drive authentication or upload service.

## Editor handoff

VlogSync 1.3.0 prepares proxy-only CapCut drafts, records manual Space batch confirmations, matches returned projects, and prepares master-backed review copies. Use native CapCut upload and Keep both retrieval. Confirm only completed transfers. Ambiguous or mismatched returns are held. After reviewing the exact saved revision, set the final title and approve the batch for native 1080p HEVC export. Final videos use the configured Edited Videos folder. Local handoff journals are stored under the VlogSync local application-data directory and should be preserved across upgrades.
