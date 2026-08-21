# Q2980: error path leaves tokens but no user in FarcasterApi.ts

## Question
When the post-login wallet creation step throws, does FarcasterApi.initializeAuth leave the freshly stored tokens in place while never invoking setUser, leaving a live session the app believes does not exist?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Force maybeCreateWalletOnLogin to reject and inspect storage and the app callback.
- Invariant to test: A login that does not complete must not leave usable credentials behind.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the create step reject and assert storage holds no privy:token afterwards.
