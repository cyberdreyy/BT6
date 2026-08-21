# Q0579: expiry skew accepts a stale token in toSearchParams.ts

## Question
tokenIsActive applies a 30 second skew over an unverified exp; can an attacker exploit clock skew or a crafted exp so toSearchParams (skips null/undefined treats an expired credential as active and skips refresh?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Set a system clock offset or craft exp and observe the refresh being skipped.
- Invariant to test: Token validity decisions must not depend on client clock or unverified claims.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: freeze Date.now past exp+skew and assert toSearchParams (skips null/undefined triggers a refresh.
