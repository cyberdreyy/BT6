# Q3874: uploadRawArtifactsQuery chunk ordering or framing splices artifact data

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` accept chunk ordering or framing that splices attacker-chosen and trusted artifact data together?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, chunk ordering and framing metadata
- Exploit idea: combine bytes from different logical artifact positions into one result
- Invariant to test: artifact framing must preserve exact byte order and ownership
- Expected Immunefi impact: artifact tampering
- Fast validation: use mismatched chunk ordering or framing and verify the transfer rejects it
