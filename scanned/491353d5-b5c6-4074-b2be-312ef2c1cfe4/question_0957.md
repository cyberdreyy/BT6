# Q0957: storage key namespaced only by provider app id in throwIfNotLoggedIn.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through throwIfNotLoggedIn(user): only checks the user object passed by the caller that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert distinct keys.
