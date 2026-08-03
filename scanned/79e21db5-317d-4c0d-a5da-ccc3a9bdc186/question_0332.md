# Q0332: Archive stale preexisting archive reused for the current job

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` reuse a stale archive file from an earlier job instead of rebuilding from current workspace contents?

## Target
- File/function: commands/helpers/archive/ziplegacy/zip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, stale archive files and repeated jobs
- Exploit idea: leave a preexisting archive where current job logic trusts it as fresh output
- Invariant to test: archive content must be bound to the current job workspace, not stale files
- Expected Immunefi impact: cross-job state reuse or artifact/cache hijack
- Fast validation: seed a stale archive before rerunning and verify fresh output is always rebuilt
