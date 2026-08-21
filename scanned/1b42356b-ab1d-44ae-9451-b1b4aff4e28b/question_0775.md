# Q0775: unlink of the last identity leaves an orphan session in pkce.ts

## Question
Can an attacker call generateState's unlink path to remove the only linked account that authenticated the session, then keep using the still-valid stored tokens on the now-unreachable account?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Unlink the sole identity, then call privy.getAccessToken() and a wallet operation with the retained credentials.
- Invariant to test: Removing the last authentication factor must invalidate the local session credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: unlink the last account then assert Session.destroyLocalState ran and getAccessToken returns null.
