# Q2679: captureContainerLogs cleanup acts on attacker-selected aliases

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `captureContainerLogs` clean up an attacker-selected alias so the wrong workload or state is removed while hostile residue remains?

## Target
- File/function: executors/docker/services.go: captureContainerLogs
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, alias paths, colliding names, and cleanup timing
- Exploit idea: redirect cleanup to the wrong workload or path and keep attacker residue
- Invariant to test: cleanup must target only the current job workload and all of its state
- Expected Immunefi impact: cross-job tampering or persistent hostile state
- Fast validation: race cleanup with aliases and verify only the intended workload state changes
