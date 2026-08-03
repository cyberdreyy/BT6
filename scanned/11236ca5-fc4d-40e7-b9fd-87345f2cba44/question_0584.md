# Q0584: createArtifactsContentProvider parallel download mixes artifact versions

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `createArtifactsContentProvider` mix chunks from different artifact versions into one trusted local result?

## Target
- File/function: network/gitlab.go: createArtifactsContentProvider
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, parallel downloads and changing artifact versions
- Exploit idea: swap artifact versions across chunk boundaries
- Invariant to test: all downloaded bytes must come from one bound artifact version
- Expected Immunefi impact: artifact tampering or stale-state reuse
- Fast validation: change artifact versions during parallel download and verify mixed output is impossible
