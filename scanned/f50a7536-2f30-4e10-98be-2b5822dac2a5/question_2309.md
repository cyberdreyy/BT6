# Q2309: startAndWatchContainer name collision reuses another job workload

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `startAndWatchContainer` collide names, labels, or selectors so one job reuses another job’s container or service?

## Target
- File/function: executors/docker/docker.go: startAndWatchContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, colliding names, labels, or selectors
- Exploit idea: make workload identity non-unique across jobs or attempts
- Invariant to test: workload naming and selection must remain unique per live job
- Expected Immunefi impact: job hijack or cross-job secret exposure
- Fast validation: run colliding jobs and verify selectors never cross job boundaries
