# Q3872: uploadRawArtifactsQuery content provider validates one file and reads another

## Question
Can an unprivileged GitLab user or pipeline author enter through artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing and make `uploadRawArtifactsQuery` validate one local artifact file but read another after replacement, rename, or late mutation?

## Target
- File/function: network/gitlab.go: uploadRawArtifactsQuery
- Entrypoint: artifact upload/download over the coordinator transfer path using attacker-controlled artifact bytes, metadata, and retry timing
- Attacker controls: artifact bytes, metadata, names, partial local files, and retry timing, renamed local files and late mutation
- Exploit idea: separate file validation from the final file read during transfer
- Invariant to test: artifact providers must remain bound to one exact local file identity
- Expected Immunefi impact: wrong-file disclosure or artifact tampering
- Fast validation: replace the local artifact file after validation and verify the provider detects it
