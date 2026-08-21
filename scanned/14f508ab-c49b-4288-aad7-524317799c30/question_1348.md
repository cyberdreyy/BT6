# Q1348: 20 second abort mid-write in toAbortSignalTimeout.ts

## Question
toAbortSignalTimeout aborts requests at 20s; can an attacker time an abort so toAbortSignalTimeout (20s request abort signal) completes a partial storage mutation while the server-side effect still lands?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Delay the response past the abort and compare local state to server state.
- Invariant to test: An aborted request must leave local session state unchanged.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: abort a refresh mid-flight and assert storage still matches the pre-request state.
