# Current status

Phase 1 version 1.1.0 is stable for the qualified Windows laptop running CapCut 9.8.0.4213. It uses the rc4 processing implementation. Clean extraction and 19 automated tests passed without importing Process Vlogs. A complete 100% display-scale cycle recovered from one deliberately interrupted accepted render, delivered a 2,848-frame master and proxy, and restored the 1080p HEVC preset. Windows sleep, wake, shutdown and restart were observed under supervision.

A separate Windows computer has not been qualified. Ambiguous multi-job recovery requires manual review. CapCut, Python, Windows OCR and FFmpeg are separate prerequisites. Private plans, media, settings, receipts and qualification records stay outside this repository. Version 1.0.3 is preserved as a rollback package. Phase 2 has not started.

## 1.2.0-rc1

The Windows loopback UI and simulated controller are available in the isolated source candidate. HTML/CSS/JavaScript require no frontend build. Fixture tests cover hostile-origin and missing-token requests, stale and duplicate approval, pause/resume, reopening, numbering collisions and unqualified-workspace holds. Browser screenshots cover both tabs. Native CapCut, live camera attach/open and real publication with this interface are unqualified. Production remains on its current controller.
