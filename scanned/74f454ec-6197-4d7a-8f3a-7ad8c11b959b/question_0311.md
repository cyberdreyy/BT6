# Q0311: Archive base-directory confusion mixes sibling worktrees

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` package files from a sibling project or worktree because the packaging base resolves differently from the filtering base?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, sibling worktrees and path aliases
- Exploit idea: select one base for validation and another for final file reads
- Invariant to test: packaging must stay within the current project workspace
- Expected Immunefi impact: cross-project artifact or cache poisoning
- Fast validation: set up sibling worktrees and verify only the current workspace is packaged
