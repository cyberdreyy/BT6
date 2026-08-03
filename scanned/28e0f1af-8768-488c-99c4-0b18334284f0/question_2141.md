# Q2141: createBuildVolume workspace mount escapes the assigned roots

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` mount or reuse a workspace path outside the assigned build, cache, or temp roots?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, workspace aliases and colliding local paths
- Exploit idea: steer mount resolution onto a runner-owned path outside the job boundary
- Invariant to test: mounted workspace and cache paths must remain inside assigned roots
- Expected Immunefi impact: supported non-privileged isolation break or cross-job tampering
- Fast validation: prepare alias paths and verify mounted paths stay confined
