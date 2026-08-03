# Q1115: selectPresignedURL cache metadata overrides trusted runtime config

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `selectPresignedURL` accept cache-derived metadata or env that overwrites trusted runtime config such as cache URLs, helper paths, or auth state?

## Target
- File/function: commands/helpers/cache_extractor.go: selectPresignedURL
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, cache metadata and env-like fields
- Exploit idea: carry attacker-controlled metadata into trusted runtime settings
- Invariant to test: cache metadata must not override trusted runtime configuration across a trust boundary
- Expected Immunefi impact: secret exposure or later job hijack
- Fast validation: restore cache with hostile metadata and verify runtime config remains trusted
