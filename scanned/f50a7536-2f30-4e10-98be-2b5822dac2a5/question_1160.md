# Q1160: tryPresignedParallelDownload cache path collides with artifact or build state

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `tryPresignedParallelDownload` place cache state onto a path that overlaps artifact output or build state, causing one trust boundary to corrupt another?

## Target
- File/function: commands/helpers/cache_extractor.go: tryPresignedParallelDownload
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, overlapping cache and artifact/build paths
- Exploit idea: collide cache local paths with other trusted runner state
- Invariant to test: cache files must stay isolated from artifact and build-state paths
- Expected Immunefi impact: cross-job tampering or output corruption
- Fast validation: set overlapping paths and verify cache state cannot land on artifact or build paths
