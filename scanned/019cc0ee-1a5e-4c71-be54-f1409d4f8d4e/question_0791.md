# Q0791: addExtractCacheCommand retry preserves stale body or metadata

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `addExtractCacheCommand` retry using stale body, headers, or metadata after the selected cache target changed?

## Target
- File/function: shells/abstract.go: addExtractCacheCommand
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache keys, fallback keys, cache paths, local cache files, and repeated jobs on one runner, retry timing and mutable local files
- Exploit idea: replay prior request state for a later logical cache target
- Invariant to test: retries must stay bound to the original target or restart with fresh state
- Expected Immunefi impact: wrong-cache mutation or stale-state reuse
- Fast validation: mutate local state between retries and verify the retried request is rebound or rejected
