# Q1269: downloadParallel source selection picks the wrong cache object

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `downloadParallel` resolve the wrong remote or alternate source for the live cache key?

## Target
- File/function: commands/helpers/cache_extractor.go: downloadParallel
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, alternate sources, fallback sources, and repeated jobs
- Exploit idea: blur the mapping between live cache key and resolved source object
- Invariant to test: cache source selection must stay bound to the live job key and trust boundary
- Expected Immunefi impact: cross-ref cache poisoning or stale-object reuse
- Fast validation: prepare competing sources and verify only the correct one is selected
