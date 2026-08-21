# Q3437: mightHaveServerCookies gates refresh in Error.ts

## Question
hasRefreshCredentials returns true when any privy-session cookie is present; can an attacker set that marker cookie so PrivyApiError attempts a refresh flow that clears or replaces valid local state?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Set a privy-session cookie without real credentials and trigger a refresh.
- Invariant to test: Refresh eligibility must not depend on an unauthenticated marker cookie.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set only the marker cookie and assert PrivyApiError does not destroy local state on the resulting failure.
