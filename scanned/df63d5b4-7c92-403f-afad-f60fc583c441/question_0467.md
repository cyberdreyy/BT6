# Q0467: null-key fallback serves the wrong user in Error.ts

## Question
Because tokens are also written under the null key, can PrivyApiError return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/Error.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert PrivyApiError does not return the null-keyed token of a different subject.
