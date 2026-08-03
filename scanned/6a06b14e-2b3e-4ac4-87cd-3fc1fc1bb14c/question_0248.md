# Q0248: Archive link entries preserved for later restore escape

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` preserve link metadata that a downstream restore follows out of its assigned root?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, symlink and hardlink metadata
- Exploit idea: ship link semantics that become dangerous only when a later job restores them
- Invariant to test: user archives must not embed link behavior that breaks restore containment
- Expected Immunefi impact: cross-job path escape via poisoned archive
- Fast validation: archive link entries and verify downstream consumers do not get a root escape
