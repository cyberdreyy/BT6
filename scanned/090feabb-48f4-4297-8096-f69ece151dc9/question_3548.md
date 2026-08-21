# Q3548: refresh failure destroys local state in toAbortSignalTimeout.ts

## Question
On MISSING_OR_INVALID_TOKEN, _refreshSession calls destroyLocalState; can an attacker force that error to arrive during toAbortSignalTimeout (20s request abort signal) so a legitimate session is dropped and re-authentication is redirected?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Return the error code from the refresh route while the user is mid-flow.
- Invariant to test: Session destruction must follow an authenticated signal, not any error carrying that code.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return the error from an unauthenticated response and assert toAbortSignalTimeout (20s request abort signal) does not clear stored tokens.
