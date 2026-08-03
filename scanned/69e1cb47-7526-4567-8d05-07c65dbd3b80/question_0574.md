# Q0574: generateArtifactsMetadataArgs post-selection file mutation changes packaged bytes

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `generateArtifactsMetadataArgs` validate one file but read a different version of that file after mutation or replacement before packaging?

## Target
- File/function: shells/abstract.go: generateArtifactsMetadataArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, file replacement after selection
- Exploit idea: swap file content after it is selected but before it is read into the archive
- Invariant to test: selection and packaging must remain bound to the same file identity
- Expected Immunefi impact: archive poisoning or secret-bearing file inclusion
- Fast validation: replace a selected file before read time and verify packaging detects or prevents the swap
