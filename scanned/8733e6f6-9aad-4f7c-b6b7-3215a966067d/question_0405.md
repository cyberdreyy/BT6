# Q0405: createBodyProvider exclude-rule canonicalization bypass

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `createBodyProvider` exclude one path spelling but still include the same file through another canonical alias?

## Target
- File/function: commands/helpers/artifacts_uploader.go: createBodyProvider
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, exclude patterns, case aliases, and slash variants
- Exploit idea: bypass excludes with an equivalent path representation
- Invariant to test: include and exclude matching must canonicalize the same way as final file selection
- Expected Immunefi impact: secret exposure or later overwrite via poisoned archive
- Fast validation: try equivalent path spellings against one exclude rule and verify consistent exclusion
