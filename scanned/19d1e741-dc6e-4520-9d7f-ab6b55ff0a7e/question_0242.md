# Q0242: Archive relative-path inclusion outside the assigned root

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` walk or package paths that escape the assigned workspace root via relative traversal or base-path confusion?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, relative paths and base-path confusion
- Exploit idea: select paths that validate relative to one base but read from another
- Invariant to test: artifact or cache packaging must remain inside the assigned workspace root
- Expected Immunefi impact: secret exposure or archive poisoning
- Fast validation: archive relative traversal candidates and assert only in-root files are included
