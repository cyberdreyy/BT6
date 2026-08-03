# Q2181: createContainerConfig wrong workload selected after a race or collision

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `createContainerConfig` validate one container or service identity but act on another job container, helper, or service after concurrent runner activity changes the lookup result?

## Target
- File/function: executors/docker/docker.go: createContainerConfig
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, workload timing and colliding names
- Exploit idea: race workload selection so the final action lands on a different live workload
- Invariant to test: actions must stay bound to the exact selected live workload identity
- Expected Immunefi impact: job hijack or cross-job secret exposure
- Fast validation: run colliding workloads and verify final actions stay attached to the intended one
