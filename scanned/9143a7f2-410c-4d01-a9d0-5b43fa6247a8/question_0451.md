# Q0451: normalizeArgs base-directory confusion mixes sibling worktrees

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `normalizeArgs` package files from a sibling project or worktree because the packaging base resolves differently from the filtering base?

## Target
- File/function: commands/helpers/artifacts_uploader.go: normalizeArgs
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, sibling worktrees and path aliases
- Exploit idea: select one base for validation and another for final file reads
- Invariant to test: packaging must stay within the current project workspace
- Expected Immunefi impact: cross-project artifact or cache poisoning
- Fast validation: set up sibling worktrees and verify only the current workspace is packaged
