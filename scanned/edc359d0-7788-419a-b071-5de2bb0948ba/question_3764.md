# Q3764: storeCustomerAccessToken accepts a non-string silently in UserApi.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use UserApi.get to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through UserApi.get and assert it raises rather than silently deleting.
