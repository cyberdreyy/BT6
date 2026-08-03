# Q0802: getAlternateCacheDownloadURL protected and unprotected cache boundary collapse

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `getAlternateCacheDownloadURL` omit or blur protected-status separation so an unprotected cache is restored by a protected job?

## Target
- File/function: shells/abstract.go: getAlternateCacheDownloadURL
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache keys, fallback keys, cache paths, local cache files, and repeated jobs on one runner, protected and unprotected refs
- Exploit idea: cause namespace derivation to ignore protection state during selection or reuse
- Invariant to test: protected and unprotected cache state must never share a namespace
- Expected Immunefi impact: protected-job escalation through cache poisoning
- Fast validation: seed an unprotected cache and verify a protected job cannot restore it
