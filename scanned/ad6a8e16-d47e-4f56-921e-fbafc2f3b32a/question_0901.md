# Q0901: addCacheUploadCommand key sanitization collision across refs or projects

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `addCacheUploadCommand` normalize two attacker-chosen cache identifiers into the same final namespace so lower-trust state collides with higher-trust state?

## Target
- File/function: shells/abstract.go: addCacheUploadCommand
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache keys, fallback keys, cache paths, local cache files, and repeated jobs on one runner, colliding cache identifiers
- Exploit idea: force distinct cache names to collapse to one namespace after sanitization
- Invariant to test: cache operations must stay bound to the correct project/ref/protected cache namespace
- Expected Immunefi impact: cross-ref or cross-project cache poisoning
- Fast validation: create colliding keys and verify they resolve to distinct protected namespaces
