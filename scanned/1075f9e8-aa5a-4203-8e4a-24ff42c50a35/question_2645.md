# Q2645: guest credential readable and reusable in pkce.ts

## Question
The guest credential lives in localStorage under privy:guest:<appId>; can a later unprivileged user of the same browser profile call privy.auth.oauth.generateURL() -> storage puts and be issued a session for the earlier guest account?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Read the stored credential, clear the tokens, then call the guest create path.
- Invariant to test: A guest credential must not survive a session clear in a form that re-authenticates the same account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run generateState, call destroyLocalState, then run generateState again and assert a new credential was generated.
