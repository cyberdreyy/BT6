# Q0617: UploadRawArtifacts wrong artifact state is returned after concurrent operations

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `UploadRawArtifacts` return or trust the result state from one artifact operation after another concurrent operation finished later?

## Target
- File/function: network/gitlab.go: UploadRawArtifacts
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, concurrent artifact operations and late completions
- Exploit idea: let a stale operation win final state selection
- Invariant to test: final artifact state must reflect the correct logical operation only
- Expected Immunefi impact: artifact-state confusion or false success
- Fast validation: run concurrent artifact operations and verify final state selection stays exact
