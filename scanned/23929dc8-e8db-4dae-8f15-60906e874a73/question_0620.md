# Q0620: UploadRawArtifacts final artifact result reflects an earlier stale attempt

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `UploadRawArtifacts` report or trust the result of an earlier stale artifact attempt instead of the last valid attempt?

## Target
- File/function: network/gitlab.go: UploadRawArtifacts
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, multiple attempts and late completions
- Exploit idea: let a stale attempt win final artifact result selection
- Invariant to test: final artifact result selection must choose the correct latest valid attempt
- Expected Immunefi impact: false artifact success or stale-state trust
- Fast validation: make attempts finish out of order and verify the final result stays correct
