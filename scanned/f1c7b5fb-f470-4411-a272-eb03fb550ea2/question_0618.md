# Q0618: UploadRawArtifacts concurrent artifact ops cross-bind job identities

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `UploadRawArtifacts` cross-bind mutable artifact-transfer state between concurrent jobs on the same runner?

## Target
- File/function: network/gitlab.go: UploadRawArtifacts
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, concurrent jobs and shared transfer state
- Exploit idea: share mutable artifact state across live job identities
- Invariant to test: artifact-transfer state must remain isolated per job
- Expected Immunefi impact: cross-job artifact hijack or disclosure
- Fast validation: run overlapping jobs and verify artifact state remains isolated
