# Q0366: CreateGzipArchive duplicate member names poison a later restore

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections and make `CreateGzipArchive` emit two members that collapse to the same final restore path so downstream extraction trusts attacker ordering?

## Target
- File/function: helpers/archives/gzip_create.go: CreateGzipArchive
- Entrypoint: artifact/cache archiving from attacker-controlled workspace files and `.gitlab-ci.yml` path selections
- Attacker controls: workspace file tree, symlinks, hardlinks, excluded paths, untracked files, and file metadata, duplicate names and canonical aliases
- Exploit idea: emit colliding archive members that later restore into one trusted path
- Invariant to test: one final path must not be represented by multiple archive members
- Expected Immunefi impact: cross-job state tampering after restore
- Fast validation: build an archive with colliding names and verify collision rejection
