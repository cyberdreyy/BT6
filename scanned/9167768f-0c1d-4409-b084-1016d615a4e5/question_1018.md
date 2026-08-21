# Q1018: credentials include on every request in toAbortSignalTimeout.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach toAbortSignalTimeout (20s request abort signal) with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/toAbortSignalTimeout.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to toAbortSignalTimeout (20s request abort signal) and assert path compilation rejects it.
