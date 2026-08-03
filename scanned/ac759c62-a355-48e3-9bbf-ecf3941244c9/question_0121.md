# Q0121: Extract root escape via parent segments

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact or cache extraction for a job consuming attacker-produced archive content and make `Extract` normalize crafted relative entries so restored content escapes the assigned extraction root, overwrites checkout, cache, helper, or temp state, and turns an unprotected job into protected-ref execution or secret exposure?

## Target
- File/function: commands/helpers/archive/fastzip/zip_fastzip_extractor.go: Extract
- Entrypoint: artifact or cache extraction for a job consuming attacker-produced archive content
- Attacker controls: archive bytes, entry names, path separators, absolute paths, `..` segments, links, and metadata, crafted relative paths
- Exploit idea: smuggle `..` segments through normalization so restore lands outside the assigned root
- Invariant to test: restored content must stay inside the assigned extraction root
- Expected Immunefi impact: protected-ref escalation or cross-job state tampering via path-root escape
- Fast validation: restore a crafted archive and assert nothing outside the extraction root is created or overwritten
