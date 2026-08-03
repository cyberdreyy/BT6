# Q0296: Archive metadata sidecar leaks sensitive file names or paths

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` emit side metadata that reveals hidden paths, secret-bearing file names, or trust-boundary details not intended for downstream jobs?

## Target
- File/function: commands/helpers/archive/raw/raw_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, artifact metadata and generated side files
- Exploit idea: leak sensitive path or file-selection details through metadata generation
- Invariant to test: metadata generation must not reveal protected or runner-private path information
- Expected Immunefi impact: secret or internal-path disclosure
- Fast validation: inspect generated metadata and verify no protected path information is exposed
