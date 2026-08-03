# Q3871: uploadRawArtifactsQuery coordinator handoff preserves stale state after identity changes

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` preserve trusted transfer state across a coordinator or object handoff even though the artifact identity changed?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, handoff timing and changed artifact identity
- Exploit idea: reuse transfer state after the logical artifact changed during the handoff
- Invariant to test: artifact-transfer state must be rebound whenever the logical artifact identity changes
- Expected Immunefi impact: wrong-object transfer or stale-state reuse
- Fast validation: change logical artifact identity across handoff and verify transfer state is rebound
