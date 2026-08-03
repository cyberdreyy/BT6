# Q2494: createServices service proxy or websocket reaches the wrong service

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `createServices` proxy traffic to a different service than the one authorized for the live job?

## Target
- File/function: executors/docker/services.go: createServices
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, service names, ports, URIs, and reconnect timing
- Exploit idea: confuse service lookup or live-session routing so traffic lands on the wrong endpoint
- Invariant to test: proxy routing must stay bound to the intended live job service
- Expected Immunefi impact: wrong-service access or secret exposure
- Fast validation: open multiple similar services and verify proxy routing cannot drift
