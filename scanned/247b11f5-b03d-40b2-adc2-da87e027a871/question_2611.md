# Q2611: createConfigForServiceHealthCheckContainer bootstrap script path collides with attacker files

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state and make `createConfigForServiceHealthCheckContainer` place bootstrap or helper scripts onto paths the attacker can pre-populate or collide with?

## Target
- File/function: executors/docker/services.go: createConfigForServiceHealthCheckContainer
- Entrypoint: Docker executor build/service/helper orchestration driven by attacker-controlled image, service, job output, and workspace state
- Attacker controls: image names, service definitions, job output, artifact/cache residue, container timing, and repeated jobs on one runner, pre-populated files and colliding script paths
- Exploit idea: cause runner-generated bootstrap content to reuse attacker-chosen files or names
- Invariant to test: bootstrap paths must be isolated and unique per job
- Expected Immunefi impact: stronger-context execution or helper-state hijack
- Fast validation: pre-create colliding files and verify bootstrap paths remain isolated
