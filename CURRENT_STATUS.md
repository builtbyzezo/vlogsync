# Current status

Phase 1 native export was demonstrated under supervision. Controller pause, cancel, resume, guarded native crash recovery and an explicit retry were demonstrated on disposable projects. Camera reconnection and source identity revalidation were demonstrated while the native queue was idle. The original rc3 unattended recovery and physical post-completion power tests were still open. This repository omits personal footage, review data and credentials.

At 100% display scale, two synthetic projects produced two matching master/proxy pairs after supervised recovery. The master preset was restored and read back at 100% after supervised recovery. This was supervised rc3 evidence. The public package remains a candidate.

The rc4 candidate removes the Process Vlogs runtime dependency and introduces a guarded retry when one accepted native job is demonstrably abandoned, with no final or partial files. Supervised Windows sleep and wake were observed, and a SHA-256 verified disposable destination file was saved beforehand.

RC4: clean Windows extraction and isolated Python environment passed setup and the automated suite without importing Process Vlogs. One accepted disposable CapCut master was deliberately interrupted at 100% display scale. With no surviving native media or other outstanding jobs, the controller automatically made one retry, completed the matching proxy, verified 2,848 frames per output and restored the master preset. Broader multi-job crash recovery still fails closed. The physical shutdown test and verification are tracked in the private qualification record. Production 1.0.3 is retained.
