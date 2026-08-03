# Q2093: guardGetSourcesScriptHooks after-script or cleanup executes attacker syntax

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardGetSourcesScriptHooks` build after-script or cleanup commands that execute attacker-controlled syntax in a stronger or differently trusted phase?

## Target
- File/function: shells/abstract.go: guardGetSourcesScriptHooks
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, values reused in after_script or cleanup generation
- Exploit idea: smuggle execution into a later runner-generated phase
- Invariant to test: later phases must preserve literal values and trust boundaries
- Expected Immunefi impact: stronger-context command execution
- Fast validation: inject shell syntax into later-phase inputs and verify literal preservation
