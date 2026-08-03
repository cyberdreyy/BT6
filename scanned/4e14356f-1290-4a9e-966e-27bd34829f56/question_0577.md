# Q0577: generateArtifactsMetadataArgs partial archive write leaves residue trusted later

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `generateArtifactsMetadataArgs` fail mid-write while leaving a partial archive that a later job still treats as valid?

## Target
- File/function: shells/abstract.go: generateArtifactsMetadataArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, interrupted archive writes and repeated jobs
- Exploit idea: produce a partial archive whose existence is mistaken for a complete current artifact
- Invariant to test: failed archive creation must not leave reusable partial output
- Expected Immunefi impact: cross-job archive poisoning or stale-state reuse
- Fast validation: interrupt creation mid-write and verify partial outputs are discarded
