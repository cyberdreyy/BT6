# Q2984: error path leaves tokens but no user in GuestApi.ts

## Question
When the post-login wallet creation step throws, does GuestApi.create leave the freshly stored tokens in place while never invoking setUser, leaving a live session the app believes does not exist?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Force maybeCreateWalletOnLogin to reject and inspect storage and the app callback.
- Invariant to test: A login that does not complete must not leave usable credentials behind.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the create step reject and assert storage holds no privy:token afterwards.
