# Q0553: writeUploadArtifacts archive naming collision across refs or jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload from attacker-controlled workspace output and artifact metadata and make `writeUploadArtifacts` choose an archive name or output path that collides across refs, stages, or jobs on the same runner?

## Target
- File/function: shells/abstract.go: writeUploadArtifacts
- Entrypoint: artifact upload from attacker-controlled workspace output and artifact metadata
- Attacker controls: workspace files, artifact paths, excludes, names, format choices, and retry timing, archive names, stage names, and repeated jobs
- Exploit idea: cause archives from different trust contexts to resolve to the same local output path
- Invariant to test: local archive outputs must stay unique per job and trust boundary
- Expected Immunefi impact: cross-job artifact confusion or poisoning
- Fast validation: run colliding jobs and verify distinct local archive outputs
