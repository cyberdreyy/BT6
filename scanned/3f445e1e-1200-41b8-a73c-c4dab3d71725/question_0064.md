# Q0064: extractZipSymlinkEntry symlink pivot to a trusted external path

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `extractZipSymlinkEntry` use symlink entries so writes go through an in-root alias onto a trusted file outside the restore root?

## Target
- File/function: helpers/archives/zip_extract.go: extractZipSymlinkEntry
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, symlink entries and symlink targets
- Exploit idea: place or extract a link first, then write through it during restore
- Invariant to test: restore must not follow attacker-controlled links to destinations outside the root
- Expected Immunefi impact: cross-job tampering or secret exposure through symlink escape
- Fast validation: restore a link-plus-file archive and verify no external target is modified
