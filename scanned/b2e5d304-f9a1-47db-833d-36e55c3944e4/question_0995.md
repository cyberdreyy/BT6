# Q0995: concurrent login writes interleave active-user pointer in pkce.ts

## Question
Can an unprivileged attacker race two privy.auth.oauth.generateURL() -> storage puts calls so storeActiveUserId writes user B while the later-resolving login stores user A's tokens under the null key, making privy:active-user point at the wrong credentials?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Fire both logins, delay one response, then read getActiveUserId and getCustomerAccessToken.
- Invariant to test: privy:active-user and the null-keyed token copy must always describe the same subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: interleave two generateState promises with controlled resolution order and assert Token.parse(getCustomerAccessToken()).subject === getActiveUserId().
