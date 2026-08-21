# Q2334: storage accessibility probe leaks a key in UserApi.ts

## Question
isStorageAccessible writes privy:__storage__test-<uuid> before every refresh; can an attacker use the residue or the failure path of UserApi.get to influence whether refresh proceeds?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Make the probe fail transiently and observe the refresh being skipped while credentials remain.
- Invariant to test: A storage probe failure must not silently change session lifecycle decisions.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make put throw once and assert UserApi.get surfaces the error rather than continuing with stale state.
