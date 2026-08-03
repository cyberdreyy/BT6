# Q0576: generateArtifactsMetadataArgs metadata sidecar leaks sensitive file names or paths

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `generateArtifactsMetadataArgs` emit side metadata that reveals hidden paths, secret-bearing file names, or trust-boundary details not intended for downstream jobs?

## Target
- File/function: shells/abstract.go: generateArtifactsMetadataArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, artifact metadata and generated side files
- Exploit idea: leak sensitive path or file-selection details through metadata generation
- Invariant to test: metadata generation must not reveal protected or runner-private path information
- Expected Immunefi impact: secret or internal-path disclosure
- Fast validation: inspect generated metadata and verify no protected path information is exposed
