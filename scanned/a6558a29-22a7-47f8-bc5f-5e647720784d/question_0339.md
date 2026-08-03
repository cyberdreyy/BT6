# Q0339: Archive compressed body provider reads a mutated file version

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `Archive` build its upload body from a file that changed after validation so the transmitted bytes do not match the trusted selection step?

## Target
- File/function: commands/helpers/archive/ziplegacy/zip_legacy_archiver.go: Archive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, file mutation between validation and compression
- Exploit idea: change the archive input after validation but before the body provider reads it
- Invariant to test: validated file identity and transmitted bytes must remain bound
- Expected Immunefi impact: artifact poisoning or wrong-file disclosure
- Fast validation: mutate a selected file after validation and verify upload body generation detects it
