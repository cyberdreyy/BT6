# Q2664: user.get returns refreshed foreign user in UserApi.ts

## Question
UserApi.get returns whatever refreshSession yields; can an attacker interleave a switch so UserApi.get returns another user's profile to code that just authorised an action for the first user?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Switch active user during the in-flight refresh and inspect the returned user.
- Invariant to test: A user read must be atomic with respect to active-user changes.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch users mid-refresh and assert UserApi.get throws rather than returning the other profile.
