# Q0693: determineUploadState lower-trust artifact bytes reach a higher-trust restore

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `determineUploadState` let artifact-transfer state from a lower-trust job feed a higher-trust restore or dependency path?

## Target
- File/function: network/gitlab.go: determineUploadState
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, protected and unprotected jobs plus artifact dependencies
- Exploit idea: carry lower-trust artifact identity across a trust boundary
- Invariant to test: artifact transfer and restore state must remain bound to the correct trust boundary
- Expected Immunefi impact: protected-job escalation through artifact poisoning
- Fast validation: seed lower-trust artifact state and verify higher-trust jobs do not inherit it
