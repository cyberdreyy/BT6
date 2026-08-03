# Q3912: Do replayed offset or cursor restarts incorrectly

## Question
Can an unprivileged GitLab user or pipeline author enter through retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests and make `Do` reuse a prior body cursor or offset on a later attempt and restart at the wrong position?

## Target
- File/function: network/retry_requester.go: Do
- Entrypoint: retrying HTTP transfer paths where attacker-controlled artifact or trace bytes are sent across repeated requests
- Attacker controls: request body bytes, retry timing, body mutation between retries, offsets, and response ordering, body cursors, offsets, and retries
- Exploit idea: replay cursor state from an earlier attempt
- Invariant to test: offset and cursor state must remain exact for the active attempt
- Expected Immunefi impact: output tampering or wrong-body transfer
- Fast validation: replay prior cursors and verify later attempts reject or fully reset them
