# Q2373: getBuildContainer target changes after selection but before exec or attach

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `getBuildContainer` select one workload and then exec, attach, or copy after the target identity has changed?

## Target
- File/function: executors/docker/docker_command.go: getBuildContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, live restarts, replacements, or renamed targets
- Exploit idea: let final action occur after the selected target has been replaced
- Invariant to test: selection and action must remain bound to one stable workload identity
- Expected Immunefi impact: job hijack or wrong-workload access
- Fast validation: replace targets after selection and verify final actions fail or rebind safely
