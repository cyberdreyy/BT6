# Q0736: newCacheConfig cached credential state keyed too broadly

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `newCacheConfig` reuse cached credential or role state across different projects, refs, or protection levels?

## Target
- File/function: shells/abstract.go: newCacheConfig
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache keys, fallback keys, cache paths, local cache files, and repeated jobs on one runner, repeated jobs, refs, and protection levels
- Exploit idea: cause auth helper or role state to key on too-broad context
- Invariant to test: credential caching must stay bound to the correct project, ref, and trust boundary
- Expected Immunefi impact: cross-job credential misuse or wrong-cache access
- Fast validation: run jobs across trust boundaries and verify no credential reuse occurs
