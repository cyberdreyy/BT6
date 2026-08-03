# Q0315: Archive alternate temp/archive rename collides with trusted files

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` rename a temporary archive onto an attacker-chosen existing file that later jobs treat as trusted?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, temp archive names and colliding destination files
- Exploit idea: steer temp-to-final rename onto a colliding trusted path
- Invariant to test: temporary archive handling must not overwrite unrelated trusted files
- Expected Immunefi impact: cross-job state tampering through local archive collision
- Fast validation: create colliding local files and verify temp rename stays within a unique path
