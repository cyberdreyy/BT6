# Q0561: generateArtifactsMetadataArgs symlink-based inclusion outside the workspace

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `generateArtifactsMetadataArgs` include bytes from outside the assigned workspace root by archiving an in-root link that resolves to a trusted external file?

## Target
- File/function: shells/abstract.go: generateArtifactsMetadataArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, in-root links to external targets
- Exploit idea: smuggle external files into the archive through linked workspace paths
- Invariant to test: archived content must originate from inside the assigned workspace root
- Expected Immunefi impact: secret exposure or later restore poisoning
- Fast validation: archive a workspace link to an external file and verify the external target is excluded
