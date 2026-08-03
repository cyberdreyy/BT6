# Q1943: generateSaveScript arg expansion evaluates attacker syntax

## Question
Can an unprivileged GitLab user or pipeline author enter through runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata and make `generateSaveScript` apply argument expansion where literal passing was required, turning attacker-controlled content into shell or helper syntax?

## Target
- File/function: shells/bash.go: generateSaveScript
- Entrypoint: runner-generated bash or PowerShell created from attacker-controlled refs, variables, paths, or step metadata
- Attacker controls: variable names and values, refs, file paths, script text, dotenv files, and section names, arguments that contain shell-significant sequences
- Exploit idea: route literal-looking input through an expansion path
- Invariant to test: argument construction must preserve literal meaning across shells
- Expected Immunefi impact: stronger-context command execution or wrong-target helper invocation
- Fast validation: pass expansion-sensitive arguments and verify they stay literal end to end
