# Q3867: uploadRawArtifactsQuery response state from one artifact op is applied to another

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` apply response status, headers, or state from one artifact operation to a different artifact operation?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, overlapping artifact operations and reordered responses
- Exploit idea: cross-bind artifact response handling across operations
- Invariant to test: artifact response handling must remain bound to the originating operation
- Expected Immunefi impact: artifact-state confusion or wrong-result trust
- Fast validation: overlap artifact operations and verify responses never cross-bind
