# Q0455: normalizeArgs alternate temp/archive rename collides with trusted files

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `normalizeArgs` rename a temporary archive onto an attacker-chosen existing file that later jobs treat as trusted?

## Target
- File/function: commands/helpers/artifacts_uploader.go: normalizeArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, temp archive names and colliding destination files
- Exploit idea: steer temp-to-final rename onto a colliding trusted path
- Invariant to test: temporary archive handling must not overwrite unrelated trusted files
- Expected Immunefi impact: cross-job state tampering through local archive collision
- Fast validation: create colliding local files and verify temp rename stays within a unique path
