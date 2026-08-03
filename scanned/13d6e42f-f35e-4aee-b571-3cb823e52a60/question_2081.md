# Q2081: guardGetSourcesScriptHooks literal data turns into shell execution

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardGetSourcesScriptHooks` turn attacker-controlled text that should stay literal into shell syntax that executes in a stronger runner context?

## Target
- File/function: shells/abstract.go: guardGetSourcesScriptHooks
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, characters meaningful to the target shell
- Exploit idea: smuggle shell syntax through quoting or interpolation boundaries
- Invariant to test: git config, checkout state, temp files, and credential-bearing helper paths used by later steps must treat attacker text as data, not executable syntax
- Expected Immunefi impact: stronger-context command execution
- Fast validation: feed shell metacharacters through the entrypoint and verify literal preservation
