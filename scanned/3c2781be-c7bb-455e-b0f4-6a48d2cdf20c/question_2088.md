# Q2088: guardGetSourcesScriptHooks credential helper binds tokens to the wrong host

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardGetSourcesScriptHooks` prepare credentials for one host or remote and then send them to another after host or URL normalization changes?

## Target
- File/function: shells/abstract.go: guardGetSourcesScriptHooks
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, remote URLs, insteadOf rules, and host aliases
- Exploit idea: change the effective remote after credentials were prepared
- Invariant to test: credential binding must remain attached to the final intended host
- Expected Immunefi impact: token disclosure or unauthorized repo access
- Fast validation: use rewritten remotes and verify credentials only go to the final approved host
