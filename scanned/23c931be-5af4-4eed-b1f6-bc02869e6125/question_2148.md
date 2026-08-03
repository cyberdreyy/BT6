# Q2148: createBuildVolume volume identity collides across jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createBuildVolume` reuse one local volume identity across multiple jobs or refs on the same runner?

## Target
- File/function: executors/docker/docker.go: createBuildVolume
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, repeated jobs, colliding local names, and shared roots
- Exploit idea: make one mounted identity too broad for the job boundary
- Invariant to test: volume identity must remain unique per job trust boundary
- Expected Immunefi impact: cross-job state reuse or protected-boundary break
- Fast validation: run colliding jobs and verify they never share one volume identity
