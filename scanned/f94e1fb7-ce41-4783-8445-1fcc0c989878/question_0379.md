# Q0379: CreateGzipArchive compressed body provider reads a mutated file version

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `CreateGzipArchive` build its upload body from a file that changed after validation so the transmitted bytes do not match the trusted selection step?

## Target
- File/function: helpers/archives/gzip_create.go: CreateGzipArchive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, file mutation between validation and compression
- Exploit idea: change the archive input after validation but before the body provider reads it
- Invariant to test: validated file identity and transmitted bytes must remain bound
- Expected Immunefi impact: artifact poisoning or wrong-file disclosure
- Fast validation: mutate a selected file after validation and verify upload body generation detects it
