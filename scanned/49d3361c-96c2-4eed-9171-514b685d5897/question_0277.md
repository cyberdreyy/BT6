# Q0277: Archive partial archive write leaves residue trusted later

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` fail mid-write while leaving a partial archive that a later job still treats as valid?

## Target
- File/function: commands/helpers/archive/gziplegacy/gzip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, interrupted archive writes and repeated jobs
- Exploit idea: produce a partial archive whose existence is mistaken for a complete current artifact
- Invariant to test: failed archive creation must not leave reusable partial output
- Expected Immunefi impact: cross-job archive poisoning or stale-state reuse
- Fast validation: interrupt creation mid-write and verify partial outputs are discarded
