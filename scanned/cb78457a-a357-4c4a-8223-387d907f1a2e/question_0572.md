# Q0572: generateArtifactsMetadataArgs stale preexisting archive reused for the current job

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `generateArtifactsMetadataArgs` reuse a stale archive file from an earlier job instead of rebuilding from current workspace contents?

## Target
- File/function: shells/abstract.go: generateArtifactsMetadataArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, stale archive files and repeated jobs
- Exploit idea: leave a preexisting archive where current job logic trusts it as fresh output
- Invariant to test: archive content must be bound to the current job workspace, not stale files
- Expected Immunefi impact: cross-job state reuse or artifact/cache hijack
- Fast validation: seed a stale archive before rerunning and verify fresh output is always rebuilt
