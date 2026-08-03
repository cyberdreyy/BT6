# Q2656: captureContainersLogs log or attach streams mix bytes across workloads

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `captureContainersLogs` mix bytes from multiple workloads into one attacker-visible stream?

## Target
- File/function: executors/docker/services.go: captureContainersLogs
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, concurrent workload output and reconnect timing
- Exploit idea: cause stream multiplexing to lose workload ownership
- Invariant to test: log and attach streams must preserve workload ownership for every byte
- Expected Immunefi impact: secret exposure or output tampering
- Fast validation: emit distinct markers from multiple workloads and verify streams never mix them
