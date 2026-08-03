# Q0590: createArtifactsContentProvider download path collides with trusted workspace files

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `createArtifactsContentProvider` write artifact download output onto paths later treated as trusted workspace state?

## Target
- File/function: network/gitlab.go: createArtifactsContentProvider
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, download paths and colliding workspace files
- Exploit idea: land artifact bytes on trusted current-job paths through path collisions
- Invariant to test: artifact download output must remain isolated from trusted workspace inputs
- Expected Immunefi impact: stronger-context execution or output tampering
- Fast validation: collide download paths with trusted workspace files and verify isolation
