# Q0303: Archive helper or temp file inclusion in the archive

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` include runner temp files, helper outputs, or credential-bearing files that were never meant for user-controlled archive export?

## Target
- File/function: commands/helpers/archive/tarzstd/tarzstd_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, temp-file names and helper-created files
- Exploit idea: cause packaging to include runner-created files that are adjacent to user output
- Invariant to test: runner temp, helper, and credential files must never enter user archives
- Expected Immunefi impact: secret exposure across trust boundaries
- Fast validation: place files near helper/temp paths and verify they are excluded
