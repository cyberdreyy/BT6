# Q2008: bashDetectScript credential helper binds tokens to the wrong host

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `bashDetectScript` prepare credentials for one host or remote and then send them to another after host or URL normalization changes?

## Target
- File/function: shells/bash.go: bashDetectScript
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, remote URLs, insteadOf rules, and host aliases
- Exploit idea: change the effective remote after credentials were prepared
- Invariant to test: credential binding must remain attached to the final intended host
- Expected Immunefi impact: token disclosure or unauthorized repo access
- Fast validation: use rewritten remotes and verify credentials only go to the final approved host
