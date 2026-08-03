# Q2096: guardGetSourcesScriptHooks host normalization misroutes authentication

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardGetSourcesScriptHooks` treat two remote hosts or paths as equivalent for auth purposes even though they are distinct security principals?

## Target
- File/function: shells/abstract.go: guardGetSourcesScriptHooks
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, visually similar hosts, schemes, or paths
- Exploit idea: make auth scope broader than the final effective remote
- Invariant to test: auth scope must remain attached to the exact final remote principal
- Expected Immunefi impact: credential disclosure or unauthorized repo access
- Fast validation: use equivalent-looking hosts and verify auth does not cross principals
