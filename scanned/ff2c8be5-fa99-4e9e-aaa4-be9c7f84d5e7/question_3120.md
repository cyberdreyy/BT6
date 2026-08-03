# Q3120: createBuildAndHelperContainers lower-trust output influences later orchestration decisions

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `createBuildAndHelperContainers` use lower-trust output from the current job to steer later orchestration for a stronger-trust job?

## Target
- File/function: executors/kubernetes/kubernetes.go: createBuildAndHelperContainers
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, workload output and repeated jobs
- Exploit idea: persist or cache attacker-controlled orchestration hints across jobs
- Invariant to test: orchestration for later jobs must not be influenced by prior lower-trust output
- Expected Immunefi impact: protected-boundary break or later job hijack
- Fast validation: seed hostile orchestration-visible output and verify later jobs ignore it
