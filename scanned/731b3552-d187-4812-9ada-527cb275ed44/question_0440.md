# Q0440: artifactFilename later restore interprets packaged names differently

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `artifactFilename` emit names that appear contained on the current platform but restore into stronger-context paths on another platform or path layer?

## Target
- File/function: commands/helpers/artifacts_uploader.go: artifactFilename
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, cross-platform path spellings
- Exploit idea: rely on a platform mismatch between packaging and downstream restore semantics
- Invariant to test: packaged names must be safe under downstream restore semantics too
- Expected Immunefi impact: cross-job overwrite or path-root escape after restore
- Fast validation: package cross-platform path forms and verify downstream-safe normalization
