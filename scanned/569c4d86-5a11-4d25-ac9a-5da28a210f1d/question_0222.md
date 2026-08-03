# Q0222: openArchive absolute-path restore outside the assigned root

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `openArchive` accept absolute or drive-qualified paths that bypass the assigned restore root and replace stronger-context files?

## Target
- File/function: commands/helpers/artifacts_downloader.go: openArchive
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, absolute paths, drive paths, UNC-style paths
- Exploit idea: feed absolute destination aliases that are trusted after path cleaning
- Invariant to test: absolute or drive-qualified member names must never escape the assigned extraction root
- Expected Immunefi impact: cross-job state tampering or stronger-context file overwrite
- Fast validation: restore entries using absolute-style names and verify the operation is rejected or confined
