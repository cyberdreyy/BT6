# Q1765: wallet-signature message fully overridable in pkce.ts

## Question
In src/pkce.ts, the prepared message can be replaced by a caller-supplied message argument; can an attacker submit a message with a nonce or statement that was never issued for that address?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Call init() for address A, then call the login method with a hand-built message for address B plus a matching signature.
- Invariant to test: The message submitted for authentication must be the one generateState prepared for that exact address and nonce.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call init() then login with a substituted message and assert the SDK rejects the mismatch.
