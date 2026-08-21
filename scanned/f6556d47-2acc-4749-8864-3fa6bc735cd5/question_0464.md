# Q0464: null-key fallback serves the wrong user in UserApi.ts

## Question
Because tokens are also written under the null key, can UserApi.get return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/client/UserApi.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert UserApi.get does not return the null-keyed token of a different subject.
