# Q2667: user.get returns refreshed foreign user in Error.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so PrivyApiError returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert PrivyApiError throws rather than returning the other profile.
