# Q1092: fetchPresignedTimestamp alternate archive rename preserves attacker cache

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `fetchPresignedTimestamp` rename or keep an alternate archive in a way that causes attacker-controlled cache state to outlive the job and win later resolution?

## Target
- File/function: commands/helpers/cache_extractor.go: fetchPresignedTimestamp
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, alternate archives and repeated jobs
- Exploit idea: let alternate local cache files survive and outrank the correct later cache
- Invariant to test: alternate local cache handling must not let lower-trust state persist across jobs
- Expected Immunefi impact: cross-job cache poisoning
- Fast validation: seed alternate archives and verify later jobs do not consume them
