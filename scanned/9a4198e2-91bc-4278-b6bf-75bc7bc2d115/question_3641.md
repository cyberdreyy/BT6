# Q3641: link succeeds against the wrong active user in FarcasterV2Api.ts

## Question
In multi-user mode, can an attacker switch the active user between the request and the refresh inside FarcasterV2Api.initializeAuth so a credential is linked to one account but reported on another?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Call the link method and switch active user while the request is in flight.
- Invariant to test: A link operation must apply to and report on a single, unchanged user id.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch active user mid-flight and assert FarcasterV2Api.initializeAuth fails rather than reporting success on the new user.
