# Q2213: createVolumes validated local path changes before final use

## Question
Can an unprivileged GitLab user or pipeline author enter through Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state and make `createVolumes` validate one local mount path but use another after renames, link swaps, or directory replacement?

## Target
- File/function: executors/docker/docker.go: createVolumes
- Entrypoint: Docker executor workspace/cache setup from attacker-controlled checkout paths, cache paths, and job-created filesystem state
- Attacker controls: workspace path state, cache paths, symlinks, repeated jobs, temp files, and job-created residue, local path races and renamed directories
- Exploit idea: change the mounted real path after validation but before final use
- Invariant to test: validation and final mount use must stay bound to the same real path
- Expected Immunefi impact: path-root escape or cross-job tampering
- Fast validation: race a local path swap and verify the final mount stays confined
