# Q3434: mightHaveServerCookies gates refresh in UserApi.ts

## Question
hasRefreshCredentials returns true when any privy-session cookie is present; can an attacker set that marker cookie so UserApi.get attempts a refresh flow that clears or replaces valid local state?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Set a privy-session cookie without real credentials and trigger a refresh.
- Invariant to test: Refresh eligibility must not depend on an unauthenticated marker cookie.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set only the marker cookie and assert UserApi.get does not destroy local state on the resulting failure.
