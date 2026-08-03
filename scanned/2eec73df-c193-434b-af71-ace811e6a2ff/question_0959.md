# Q0959: upload alternate source fallback crosses namespace boundaries

## Question
Can an unprivileged GitLab user or pipeline author enter through `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents and make `upload` step into an alternate source or fallback that belongs to a different project, ref, or protection boundary?

## Target
- File/function: commands/helpers/cache_archiver.go: upload
- Entrypoint: `.gitlab-ci.yml` cache restore/upload using attacker-controlled cache keys, fallback keys, and writable cache contents
- Attacker controls: cache archives, fallback order, timestamps, etags, alternate local files, and retry timing, alternate sources and fallback order
- Exploit idea: use fallback resolution to cross a namespace boundary that primary lookup would respect
- Invariant to test: fallback sources must respect the same trust boundary as primary sources
- Expected Immunefi impact: cross-project or protected-boundary cache poisoning
- Fast validation: configure multi-source fallback and verify cross-boundary sources are never accepted
