# Q3904: Do body regeneration reads a mutated file version

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `Do` regenerate a request body from a local file that changed after validation?

## Target
- File/function: network/retry_requester.go: Do
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, body regeneration and local file mutation
- Exploit idea: read a different local file version on retry than the one initially approved
- Invariant to test: regenerated bodies must remain bound to the validated file identity
- Expected Immunefi impact: wrong-file transfer or output tampering
- Fast validation: mutate the local file between retries and verify regeneration detects it
