# Q3768: storeCustomerAccessToken accepts a non-string silently in toAbortSignalTimeout.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use toAbortSignalTimeout (20s request abort signal) to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through toAbortSignalTimeout (20s request abort signal) and assert it raises rather than silently deleting.
