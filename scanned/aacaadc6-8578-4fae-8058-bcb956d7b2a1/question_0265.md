# Q0265: Archive exclude-rule canonicalization bypass

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` exclude one path spelling but still include the same file through another canonical alias?

## Target
- File/function: commands/helpers/archive/gziplegacy/gzip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, exclude patterns, case aliases, and slash variants
- Exploit idea: bypass excludes with an equivalent path representation
- Invariant to test: include and exclude matching must canonicalize the same way as final file selection
- Expected Immunefi impact: secret exposure or later overwrite via poisoned archive
- Fast validation: try equivalent path spellings against one exclude rule and verify consistent exclusion
