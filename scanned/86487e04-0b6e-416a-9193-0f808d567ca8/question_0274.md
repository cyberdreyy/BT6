# Q0274: Archive post-selection file mutation changes packaged bytes

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` validate one file but read a different version of that file after mutation or replacement before packaging?

## Target
- File/function: commands/helpers/archive/gziplegacy/gzip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, file replacement after selection
- Exploit idea: swap file content after it is selected but before it is read into the archive
- Invariant to test: selection and packaging must remain bound to the same file identity
- Expected Immunefi impact: archive poisoning or secret-bearing file inclusion
- Fast validation: replace a selected file before read time and verify packaging detects or prevents the swap
