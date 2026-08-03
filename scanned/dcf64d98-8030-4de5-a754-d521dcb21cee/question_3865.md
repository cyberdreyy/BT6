# Q3865: uploadRawArtifactsQuery partial local files are treated as complete artifacts

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` treat a partial local file from a failed transfer as a complete artifact for a later job?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, partial local files and repeated jobs
- Exploit idea: leave partial local artifact state where later logic trusts it as complete
- Invariant to test: failed transfers must not leave reusable partial artifacts
- Expected Immunefi impact: cross-job state poisoning or stale-state reuse
- Fast validation: interrupt transfers mid-write and verify partial files are discarded
