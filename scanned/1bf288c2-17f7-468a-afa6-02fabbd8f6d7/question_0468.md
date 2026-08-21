# Q0468: null-key fallback serves the wrong user in toAbortSignalTimeout.ts

## Question
Because tokens are also written under the null key, can toAbortSignalTimeout (20s request abort signal) return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/toAbortSignalTimeout.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert toAbortSignalTimeout (20s request abort signal) does not return the null-keyed token of a different subject.
