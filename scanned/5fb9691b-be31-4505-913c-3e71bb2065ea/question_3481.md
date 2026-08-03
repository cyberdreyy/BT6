# Q3481: stepsServiceContainers wrong workload selected after a race or collision

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `stepsServiceContainers` validate one pod, container, or mounted build state identity but act on another job pod, helper, or service container after concurrent runner activity changes the lookup result?

## Target
- File/function: executors/kubernetes/steps_pod.go: stepsServiceContainers
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, workload timing and colliding names
- Exploit idea: race workload selection so the final action lands on a different live workload
- Invariant to test: actions must stay bound to the exact selected live workload identity
- Expected Immunefi impact: job hijack or cross-job secret exposure
- Fast validation: run colliding workloads and verify final actions stay attached to the intended one
