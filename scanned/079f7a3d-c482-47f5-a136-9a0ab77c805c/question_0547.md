# Q0547: writeUploadArtifacts separator or case alias survives packaging checks

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `writeUploadArtifacts` preserve member names that look distinct at package time but collide during downstream restore because of separator or case rules?

## Target
- File/function: shells/abstract.go: writeUploadArtifacts
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, slash/backslash aliases and case variants
- Exploit idea: package names that restore differently than they were filtered
- Invariant to test: packaging must account for downstream canonicalization rules
- Expected Immunefi impact: later restore overwrite of trusted files
- Fast validation: archive case and separator aliases and verify downstream-safe normalization
