# Q0236: openArchive parallel or chunked restore mixes two objects

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `openArchive` mix bytes from old and new archive objects during parallel or chunked restore, producing a trusted but attacker-influenced output tree?

## Target
- File/function: commands/helpers/artifacts_downloader.go: openArchive
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, repeated downloads, chunk boundaries, and retries
- Exploit idea: swap archive versions across parallel or retried restore boundaries
- Invariant to test: all restored bytes must come from one bound object version
- Expected Immunefi impact: output tampering or stronger-context file overwrite
- Fast validation: change source objects during chunked restore and verify mixed output is impossible
