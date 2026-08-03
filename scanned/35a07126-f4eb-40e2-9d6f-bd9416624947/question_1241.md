# Q1241: selectGoCloudSource key sanitization collision across refs or projects

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `selectGoCloudSource` normalize two attacker-chosen cache identifiers into the same final namespace so lower-trust state collides with higher-trust state?

## Target
- File/function: commands/helpers/cache_extractor.go: selectGoCloudSource
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, colliding cache identifiers
- Exploit idea: force distinct cache names to collapse to one namespace after sanitization
- Invariant to test: cache operations must stay bound to the assigned cache root and the correct cache object for the live job
- Expected Immunefi impact: cross-ref or cross-project cache poisoning
- Fast validation: create colliding keys and verify they resolve to distinct protected namespaces
