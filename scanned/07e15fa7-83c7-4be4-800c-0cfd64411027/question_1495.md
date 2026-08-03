# Q1495: CommandArgExpand repo-controlled config influences later job behavior

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `CommandArgExpand` trust repo-controlled config, hooks, or files strongly enough that later steps execute or source attacker content?

## Target
- File/function: shells/powershell.go: CommandArgExpand
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, repo-controlled config and hook files
- Exploit idea: place attacker-controlled repository files onto implicitly trusted paths
- Invariant to test: repo content must not become stronger-context runner config without revalidation
- Expected Immunefi impact: stronger-context execution or secret exposure
- Fast validation: commit hostile repo-control files and verify runner-generated steps do not trust them
