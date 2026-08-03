# Q2055: writeGetSourcesScript repo-controlled config influences later job behavior

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeGetSourcesScript` trust repo-controlled config, hooks, or files strongly enough that later steps execute or source attacker content?

## Target
- File/function: shells/abstract.go: writeGetSourcesScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, repo-controlled config and hook files
- Exploit idea: place attacker-controlled repository files onto implicitly trusted paths
- Invariant to test: repo content must not become stronger-context runner config without revalidation
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: commit hostile repo-control files and verify runner-generated steps do not trust them
