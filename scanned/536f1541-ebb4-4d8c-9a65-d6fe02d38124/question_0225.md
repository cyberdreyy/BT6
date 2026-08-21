# Q0225: legacy null-keyed copy outlives its user in pkce.ts

## Question
Can an attacker exploit the fact that generateState stores tokens both under privy:<userId>:token and the legacy null-keyed privy:token, so a later logout or user switch clears one copy and leaves the other usable?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Log in as A, log in as B in multi-user mode, then remove B and read the null-keyed key still holding a live credential.
- Invariant to test: Every stored credential copy must be invalidated together with the session it belongs to.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run generateState for user A then user B, call Session.destroyLocalState and assert getKeys() contains no privy:*token entries.
