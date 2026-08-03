# Q2423: hasExistingContainer mount or workspace escape in supported non-privileged mode

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `hasExistingContainer` mount or reuse a path outside the intended workspace, cache, or temp roots in a supported non-privileged setup?

## Target
- File/function: executors/docker/docker_command.go: hasExistingContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, path aliases, cache residue, and workspace state
- Exploit idea: steer mounts or workspace state onto external runner-owned paths
- Invariant to test: non-privileged job state must remain confined to assigned roots
- Expected Immunefi impact: supported non-privileged isolation break or cross-job tampering
- Fast validation: prepare alias paths and verify mounted state stays within assigned roots
