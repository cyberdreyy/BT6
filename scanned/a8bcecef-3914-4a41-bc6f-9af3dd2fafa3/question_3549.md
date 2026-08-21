# Q3549: refresh failure destroys local state in toSearchParams.ts

## Question
On MISSING_OR_INVALID_TOKEN, _refreshSession calls destroyLocalState; can an attacker force that error to arrive during toSearchParams (skips null/undefined so a legitimate session is dropped and re-authentication is redirected?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Return the error code from the refresh route while the user is mid-flow.
- Invariant to test: Session destruction must follow an authenticated signal, not any error carrying that code.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return the error from an unauthenticated response and assert toSearchParams (skips null/undefined does not clear stored tokens.
