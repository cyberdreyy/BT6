# Q0138: Extract error-path cleanup follows attacker alias

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `Extract` hit an error path where cleanup follows attacker-controlled aliases and removes or rewrites files outside the intended root?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_extractor.go: Extract
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, failure timing, symlink aliases, and renamed directories
- Exploit idea: steer cleanup into attacker-selected aliases after a forced failure
- Invariant to test: failure cleanup must only touch paths proven to belong to the current restore root
- Expected Immunefi impact: cross-job tampering through cleanup path confusion
- Fast validation: induce a restore failure and verify cleanup never touches external paths
