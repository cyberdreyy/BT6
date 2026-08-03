# Q2063: writeClearWorktreeScript arg expansion evaluates attacker syntax

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `writeClearWorktreeScript` apply argument expansion where literal passing was required, turning attacker-controlled content into shell or helper syntax?

## Target
- File/function: shells/abstract.go: writeClearWorktreeScript
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, arguments that contain shell-significant sequences
- Exploit idea: route literal-looking input through an expansion path
- Invariant to test: argument construction must preserve literal meaning across shells
- Expected Immunefi impact: stronger-context command execution or wrong-target helper invocation
- Fast validation: pass expansion-sensitive arguments and verify they stay literal end to end
