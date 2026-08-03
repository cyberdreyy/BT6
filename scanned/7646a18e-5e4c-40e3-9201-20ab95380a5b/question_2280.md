# Q2280: readContainerLogs lower-trust output influences later orchestration decisions

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `readContainerLogs` use lower-trust output from the current job to steer later orchestration for a stronger-trust job?

## Target
- File/function: executors/docker/docker.go: readContainerLogs
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, workload output and repeated jobs
- Exploit idea: persist or cache attacker-controlled orchestration hints across jobs
- Invariant to test: orchestration for later jobs must not be influenced by prior lower-trust output
- Expected Immunefi impact: protected-boundary break or later job hijack
- Fast validation: seed hostile orchestration-visible output and verify later jobs ignore it
