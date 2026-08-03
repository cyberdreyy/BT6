# Q2818: Prepare readiness or watch state binds to stale workloads

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `Prepare` accept readiness or watch results from a stale workload for the live job?

## Target
- File/function: executors/kubernetes/kubernetes.go: Prepare
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, restarts, replacements, and readiness timing
- Exploit idea: reuse readiness state after the underlying workload changed identity
- Invariant to test: readiness and watch state must stay bound to the live workload instance
- Expected Immunefi impact: job hijack, wrong-service use, or stale-state trust
- Fast validation: restart workloads during readiness checks and verify stale results are rejected
