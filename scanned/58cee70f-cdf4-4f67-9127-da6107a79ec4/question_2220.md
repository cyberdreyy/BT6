# Q2220: createVolumes repeated attempts reuse the same volume unexpectedly

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` reuse one volume state across multiple attempts of the same logical job when fresh state was required?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, retries, repeated attempts, and stale volume state
- Exploit idea: let retry or rerun logic trust prior volume state instead of rebuilding it
- Invariant to test: each attempt must receive fresh or correctly rebound volume state
- Expected Immunefi impact: stale-state reuse or later job confusion
- Fast validation: repeat one job attempt and verify volume state is freshly created or safely rebound
