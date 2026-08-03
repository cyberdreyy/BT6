# Q0088: extractZipFileEntry partial restore leaves residue for a later stronger job

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `extractZipFileEntry` fail mid-restore while leaving lower-trust residue that a later protected or unrelated job consumes as if it were valid?

## Target
- File/function: helpers/archives/zip_extract.go: extractZipFileEntry
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, malformed archive boundaries and partial data
- Exploit idea: force a restore error after selected files are written but before cleanup
- Invariant to test: failed restore must not leave reusable attacker state for later jobs
- Expected Immunefi impact: cross-job state persistence and later job hijack
- Fast validation: interrupt restore mid-stream and confirm residue is removed or quarantined
