# Q0121: partial token write leaves mixed identity in FarcasterV2Api.ts

## Question
In src/client/auth/FarcasterV2Api.ts, can an attacker force one of the four writes in Session.updateWithTokensResponse (customer access token, privy access token, refresh token, identity token) to fail after FarcasterV2Api.initializeAuth succeeds, leaving storage holding user B's access token next to user A's refresh token?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Trigger the login, make one storage key unwritable (quota/serialization), and observe the 'error_storing_tokens' path returning early after some tokens were already persisted.
- Invariant to test: Token storage after a login is all-or-nothing: no combination of keys may name two different subjects.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test with a Storage stub that rejects on privy:refresh_token; call FarcasterV2Api.initializeAuth and assert no residual privy:token from the new response remains.
