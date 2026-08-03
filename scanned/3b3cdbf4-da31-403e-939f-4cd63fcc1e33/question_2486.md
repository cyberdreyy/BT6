# Q2486: createServices terminal, proxy, or exec reaches the wrong live workload

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `createServices` attach, proxy, or exec against the wrong live workload for the current job?

## Target
- File/function: executors/docker/services.go: createServices
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, reconnect timing and live workload changes
- Exploit idea: desynchronize live-session selection from the intended workload
- Invariant to test: interactive and exec paths must stay bound to the live job workload
- Expected Immunefi impact: session hijack or wrong-workload access
- Fast validation: reconnect while workloads change and verify the session cannot switch targets
