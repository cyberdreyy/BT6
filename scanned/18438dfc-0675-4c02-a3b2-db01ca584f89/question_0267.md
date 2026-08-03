# Q0267: Archive separator or case alias survives packaging checks

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` preserve member names that look distinct at package time but collide during downstream restore because of separator or case rules?

## Target
- File/function: commands/helpers/archive/gziplegacy/gzip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, slash/backslash aliases and case variants
- Exploit idea: package names that restore differently than they were filtered
- Invariant to test: packaging must account for downstream canonicalization rules
- Expected Immunefi impact: later restore overwrite of trusted files
- Fast validation: archive case and separator aliases and verify downstream-safe normalization
