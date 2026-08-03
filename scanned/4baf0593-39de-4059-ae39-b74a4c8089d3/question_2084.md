# Q2084: guardGetSourcesScriptHooks env or dotenv path escapes temp roots

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardGetSourcesScriptHooks` read from or write to an env file outside the assigned checkout root and temp config roots?

## Target
- File/function: shells/abstract.go: guardGetSourcesScriptHooks
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, env-file paths and path aliases
- Exploit idea: select env-file locations that resolve outside assigned temp roots
- Invariant to test: env and dotenv files must remain inside the assigned checkout root and temp config roots
- Expected Immunefi impact: trusted-runtime override or secret-bearing file overwrite
- Fast validation: supply escaping env-file paths and verify the operation is rejected or confined
