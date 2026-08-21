# Q2338: storage accessibility probe leaks a key in toAbortSignalTimeout.ts

## Question
isStorageAccessible writes privy:__storage__test-<uuid> before every refresh; can an attacker use the residue or the failure path of toAbortSignalTimeout (20s request abort signal) to influence whether refresh proceeds?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Make the probe fail transiently and observe the refresh being skipped while credentials remain.
- Invariant to test: A storage probe failure must not silently change session lifecycle decisions.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make put throw once and assert toAbortSignalTimeout (20s request abort signal) surfaces the error rather than continuing with stale state.
