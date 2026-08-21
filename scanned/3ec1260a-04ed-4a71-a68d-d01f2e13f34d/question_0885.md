# Q0885: update flow accepts mismatched old/new identifiers in pkce.ts

## Question
In src/pkce.ts, can an attacker submit an update request whose old identifier is not the one currently linked, so the code they hold is applied against a different identifier binding?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Call the update method with an arbitrary old value plus a valid code for another identifier and observe client-side acceptance.
- Invariant to test: generateState must bind the verification code to the exact identifier pair currently on the account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call the update method with mismatched old identifier and assert the SDK does not issue the request.
