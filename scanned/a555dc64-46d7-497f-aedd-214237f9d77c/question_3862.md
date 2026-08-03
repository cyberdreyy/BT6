# Q3862: uploadRawArtifactsQuery artifact name or metadata resolves to the wrong identity

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` map artifact name or metadata onto the wrong artifact identity for the live job?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, artifact names, metadata, and repeated jobs
- Exploit idea: blur artifact identity so one logical artifact is mistaken for another
- Invariant to test: artifact identity must remain exact for the live job and stage
- Expected Immunefi impact: artifact hijack or cross-job tampering
- Fast validation: use colliding artifact names or metadata and verify identity binding stays exact
