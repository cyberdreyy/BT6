# Q2038: guardRunnerCommand generated script save or load path is attacker-chosen

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardRunnerCommand` save or load generated script content through a path the attacker can collide with trusted files?

## Target
- File/function: shells/abstract.go: guardRunnerCommand
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, script save paths and temp-file names
- Exploit idea: steer script persistence onto a colliding or escaping path
- Invariant to test: generated scripts must only be saved and loaded from isolated temp paths
- Expected Immunefi impact: later-stage hijack or secret-bearing file overwrite
- Fast validation: collide save/load paths and verify generated scripts remain isolated
