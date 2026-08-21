# Q0574: expiry skew accepts a stale token in UserApi.ts

## Question
tokenIsActive applies a 30 second skew over an unverified exp; can an attacker exploit clock skew or a crafted exp so UserApi.get treats an expired credential as active and skips refresh?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Set a system clock offset or craft exp and observe the refresh being skipped.
- Invariant to test: Token validity decisions must not depend on client clock or unverified claims.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: freeze Date.now past exp+skew and assert UserApi.get triggers a refresh.
