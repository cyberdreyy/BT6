# Q0883: getArchiverArgs fallback key precedence lets attacker cache win

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `getArchiverArgs` select a lower-trust fallback cache ahead of the cache that belongs to the live job?

## Target
- File/function: shells/abstract.go: getArchiverArgs
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache keys, fallback keys, cache paths, local cache files, and repeated jobs on one runner, fallback key ordering
- Exploit idea: arrange fallback keys so attacker-controlled cache wins resolution unexpectedly
- Invariant to test: fallback resolution must not let lower-trust state override the live job cache
- Expected Immunefi impact: cross-ref cache poisoning or stale-state reuse
- Fast validation: prepare multiple fallback caches and verify the correct trust boundary wins
