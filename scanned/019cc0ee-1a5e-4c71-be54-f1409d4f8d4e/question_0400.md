# Q0400: createZipDirectoryEntry later restore interprets packaged names differently

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `createZipDirectoryEntry` emit names that appear contained on the current platform but restore into stronger-context paths on another platform or path layer?

## Target
- File/function: helpers/archives/zip_create.go: createZipDirectoryEntry
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, cross-platform path spellings
- Exploit idea: rely on a platform mismatch between packaging and downstream restore semantics
- Invariant to test: packaged names must be safe under downstream restore semantics too
- Expected Immunefi impact: cross-job overwrite or path-root escape after restore
- Fast validation: package cross-platform path forms and verify downstream-safe normalization
