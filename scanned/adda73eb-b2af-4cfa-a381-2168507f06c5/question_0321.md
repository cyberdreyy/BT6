# Q0321: Archive symlink-based inclusion outside the workspace

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` include bytes from outside the assigned workspace root by archiving an in-root link that resolves to a trusted external file?

## Target
- File/function: commands/helpers/archive/ziplegacy/zip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, in-root links to external targets
- Exploit idea: smuggle external files into the archive through linked workspace paths
- Invariant to test: archived content must originate from inside the assigned workspace root
- Expected Immunefi impact: secret exposure or later restore poisoning
- Fast validation: archive a workspace link to an external file and verify the external target is excluded
