# Q0569: generateArtifactsMetadataArgs metadata preserved for later stronger-context trust

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `generateArtifactsMetadataArgs` preserve executable bits, ownership, or timestamps that later cause restored files to be trusted too strongly?

## Target
- File/function: shells/abstract.go: generateArtifactsMetadataArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, mode bits, ownership metadata, and timestamps
- Exploit idea: smuggle trust-affecting metadata across the archive boundary
- Invariant to test: archive metadata must not let lower-trust content gain later stronger-context trust
- Expected Immunefi impact: stronger-context execution or protected file misuse
- Fast validation: archive attacker files with hostile metadata and verify downstream trust does not increase
