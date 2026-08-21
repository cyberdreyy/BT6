# Q2975: error path leaves tokens but no user in pkce.ts

## Question
When the post-login wallet creation step throws, does generateState leave the freshly stored tokens in place while never invoking setUser, leaving a live session the app believes does not exist?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Force maybeCreateWalletOnLogin to reject and inspect storage and the app callback.
- Invariant to test: A login that does not complete must not leave usable credentials behind.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the create step reject and assert storage holds no privy:token afterwards.
