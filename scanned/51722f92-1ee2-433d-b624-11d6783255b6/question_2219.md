# Q2219: createVolumes host and container views resolve the same path differently

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` use one path that looks in-bounds in the container view but resolves to another path in the host view?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, host and container path aliases
- Exploit idea: exploit a mismatch between host-side and container-side canonicalization
- Invariant to test: host and container path resolution must preserve one shared trust boundary
- Expected Immunefi impact: path-root escape or cross-job tampering
- Fast validation: exercise host and container path aliases and verify consistent confinement
