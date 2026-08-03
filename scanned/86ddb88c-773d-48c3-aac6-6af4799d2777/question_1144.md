# Q1144: tryPresignedParallelDownload alternate fallback keys collide after normalization

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `tryPresignedParallelDownload` treat distinct primary and fallback keys as the same namespace after normalization?

## Target
- File/function: commands/helpers/cache_extractor.go: tryPresignedParallelDownload
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, alternate key variants
- Exploit idea: collapse distinct keys into one namespace through normalization differences
- Invariant to test: primary and fallback keys must remain distinguishable after normalization
- Expected Immunefi impact: cache namespace confusion and cross-job tampering
- Fast validation: use aliased primary/fallback keys and verify no collision occurs
