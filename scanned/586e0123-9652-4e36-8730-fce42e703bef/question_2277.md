# Q2277: readContainerLogs secrets become visible in the wrong role or phase

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `readContainerLogs` expose projected secrets or credentials to a role or phase that should not receive them?

## Target
- File/function: executors/docker/docker.go: readContainerLogs
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, build, helper, service, or phase transitions
- Exploit idea: allow secret-bearing state to cross one role boundary too far
- Invariant to test: secret projection must remain limited to the intended role and phase
- Expected Immunefi impact: secret exposure across role boundaries
- Fast validation: trace secret presence across phases and verify it is never visible in attacker-controlled roles
