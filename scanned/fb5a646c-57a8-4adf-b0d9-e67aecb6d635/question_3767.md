# Q3767: storeCustomerAccessToken accepts a non-string silently in Error.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use PrivyApiError to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through PrivyApiError and assert it raises rather than silently deleting.
