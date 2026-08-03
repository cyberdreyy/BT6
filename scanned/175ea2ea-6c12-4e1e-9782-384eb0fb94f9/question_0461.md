# Q0461: download root escape via parent segments

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job and make `download` normalize crafted relative entries so restored content escapes the downstream job root, overwrites downstream checkout, cache, helper, or temp state, and turns an unprotected job into protected-ref execution or secret exposure?

## Target
- File/function: commands/helpers/artifacts_downloader.go: download
- Entrypoint: artifact download and extraction between jobs where the attacker controls artifact contents or metadata from an earlier job
- Attacker controls: artifact bytes, entry names, archive format, dependency ordering, and restore timing, crafted relative paths
- Exploit idea: smuggle `..` segments through normalization so restore lands outside the assigned root
- Invariant to test: restored content must stay inside the downstream job root
- Expected Immunefi impact: protected-ref escalation or cross-job state tampering via path-root escape
- Fast validation: restore a crafted archive and assert nothing outside the downstream job root is created or overwritten
