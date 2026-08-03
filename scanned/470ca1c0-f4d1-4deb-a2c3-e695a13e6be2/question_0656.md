# Q0656: DownloadArtifacts parallel or chunked restore mixes two objects

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `DownloadArtifacts` mix bytes from old and new archive objects during parallel or chunked restore, producing a trusted but attacker-influenced output tree?

## Target
- File/function: network/gitlab.go: DownloadArtifacts
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, repeated downloads, chunk boundaries, and retries
- Exploit idea: swap archive versions across parallel or retried restore boundaries
- Invariant to test: all restored bytes must come from one bound object version
- Expected Immunefi impact: output tampering or stronger-context file overwrite
- Fast validation: change source objects during chunked restore and verify mixed output is impossible
