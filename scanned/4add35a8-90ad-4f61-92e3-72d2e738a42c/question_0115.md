# Q0115: partial token write leaves mixed identity in pkce.ts

## Question
In src/pkce.ts, can an attacker force one of the four writes in Session.updateWithTokensResponse (customer access token, privy access token, refresh token, identity token) to fail after generateState succeeds, leaving storage holding user B's access token next to user A's refresh token?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Trigger the login, make one storage key unwritable (quota/serialization), and observe the 'error_storing_tokens' path returning early after some tokens were already persisted.
- Invariant to test: Token storage after a login is all-or-nothing: no combination of keys may name two different subjects.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test with a Storage stub that rejects on privy:refresh_token; call generateState and assert no residual privy:token from the new response remains.
