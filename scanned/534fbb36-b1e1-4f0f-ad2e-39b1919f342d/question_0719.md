# Q0719: Sanitize alternate source fallback crosses namespace boundaries

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `Sanitize` step into an alternate source or fallback that belongs to a different project, ref, or protection boundary?

## Target
- File/function: cache/cachekey/cachekey.go: Sanitize
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache keys, fallback keys, cache paths, local cache files, and repeated jobs on one runner, alternate sources and fallback order
- Exploit idea: use fallback resolution to cross a namespace boundary that primary lookup would respect
- Invariant to test: fallback sources must respect the same trust boundary as primary sources
- Expected Immunefi impact: cross-project or protected-boundary cache poisoning
- Fast validation: configure multi-source fallback and verify cross-boundary sources are never accepted
