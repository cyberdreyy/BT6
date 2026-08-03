# Q0111: extractZipFile sibling build or cache directory overwrite

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `extractZipFile` write into a sibling build or cache directory for another job on the same runner?

## Target
- File/function: helpers/archives/zip_extract.go: extractZipFile
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, names targeting sibling build/cache directories
- Exploit idea: escape into another assigned runner directory rather than the current job root
- Invariant to test: one job restore must not modify another job directory on the same runner
- Expected Immunefi impact: cross-project or cross-job state tampering
- Fast validation: attempt sibling-directory writes and verify isolation
