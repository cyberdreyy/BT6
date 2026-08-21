# Q3438: mightHaveServerCookies gates refresh in toAbortSignalTimeout.ts

## Question
hasRefreshCredentials returns true when any privy-session cookie is present; can an attacker set that marker cookie so toAbortSignalTimeout (20s request abort signal) attempts a refresh flow that clears or replaces valid local state?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Set a privy-session cookie without real credentials and trigger a refresh.
- Invariant to test: Refresh eligibility must not depend on an unauthenticated marker cookie.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set only the marker cookie and assert toAbortSignalTimeout (20s request abort signal) does not destroy local state on the resulting failure.
