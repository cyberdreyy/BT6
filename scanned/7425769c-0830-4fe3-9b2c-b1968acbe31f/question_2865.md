# Q2865: unlink then relink races the session refresh in pkce.ts

## Question
Can an attacker interleave an unlink and a link through generateState so refreshSession observes the intermediate state and the app renders a linked-account set that no longer matches the server?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Fire unlink and link back to back and inspect the user object each returns.
- Invariant to test: The user object returned by each src/pkce.ts operation must reflect the state after that operation completed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run unlink and link concurrently and assert the final returned linked_accounts equals a fresh user.get().
