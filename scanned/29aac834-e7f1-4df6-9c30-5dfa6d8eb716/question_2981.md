# Q2981: error path leaves tokens but no user in FarcasterV2Api.ts

## Question
When the post-login wallet creation step throws, does FarcasterV2Api.initializeAuth leave the freshly stored tokens in place while never invoking setUser, leaving a live session the app believes does not exist?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Force maybeCreateWalletOnLogin to reject and inspect storage and the app callback.
- Invariant to test: A login that does not complete must not leave usable credentials behind.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the create step reject and assert storage holds no privy:token afterwards.
