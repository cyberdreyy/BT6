# Q0616: UploadRawArtifacts stale download temp files survive into later jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `UploadRawArtifacts` leave a stale temporary download file on disk long enough for a later job to trust it as the current artifact?

## Target
- File/function: network/gitlab.go: UploadRawArtifacts
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, temporary files and repeated jobs
- Exploit idea: preserve stale temp files beyond the owning transfer
- Invariant to test: temporary artifact files must be isolated and cleaned before later jobs run
- Expected Immunefi impact: cross-job artifact poisoning
- Fast validation: leave stale temp files behind and verify later jobs do not trust them
