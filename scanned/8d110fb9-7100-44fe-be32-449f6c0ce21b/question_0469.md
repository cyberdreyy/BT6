# Q0469: null-key fallback serves the wrong user in toSearchParams.ts

## Question
Because tokens are also written under the null key, can toSearchParams (skips null/undefined return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/utils/toSearchParams.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert toSearchParams (skips null/undefined does not return the null-keyed token of a different subject.
