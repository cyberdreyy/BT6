# Q0329: Archive metadata preserved for later stronger-context trust

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` preserve executable bits, ownership, or timestamps that later cause restored files to be trusted too strongly?

## Target
- File/function: commands/helpers/archive/ziplegacy/zip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, mode bits, ownership metadata, and timestamps
- Exploit idea: smuggle trust-affecting metadata across the archive boundary
- Invariant to test: archive metadata must not let lower-trust content gain later stronger-context trust
- Expected Immunefi impact: stronger-context execution or protected file misuse
- Fast validation: archive attacker files with hostile metadata and verify downstream trust does not increase
