# Q3094: preparePodConfig service proxy or websocket reaches the wrong service

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs and make `preparePodConfig` proxy traffic to a different service than the one authorized for the live job?

## Target
- File/function: executors/kubernetes/kubernetes.go: preparePodConfig
- Entrypoint: Kubernetes executor pod and steps setup driven by attacker-controlled image, service, script, and workspace inputs
- Attacker controls: image names, service specs, script artifacts, workspace state, and reconnect timing, service names, ports, URIs, and reconnect timing
- Exploit idea: confuse service lookup or live-session routing so traffic lands on the wrong endpoint
- Invariant to test: proxy routing must stay bound to the intended live job service
- Expected Immunefi impact: wrong-service access or secret exposure
- Fast validation: open multiple similar services and verify proxy routing cannot drift
