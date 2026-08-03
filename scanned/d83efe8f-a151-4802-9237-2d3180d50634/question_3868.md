# Q3868: uploadRawArtifactsQuery artifact transfer state from one job is reused by another

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` reuse upload or download state from one job’s artifact operation in another job on the same runner?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, rapid sequential jobs and shared artifact state
- Exploit idea: hold mutable artifact-transfer state beyond the owning job
- Invariant to test: artifact-transfer state must remain isolated per live job
- Expected Immunefi impact: cross-job artifact hijack or disclosure
- Fast validation: run sequential jobs and verify artifact state does not leak forward
