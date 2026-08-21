# Q3525: oauth_tokens emitted to any listener in pkce.ts

## Question
Provider tokens from generateState are emitted through the session 'oauth_tokens_granted' event to every registered listener; can an attacker register or keep a listener that receives another flow's provider tokens?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Attach a listener, trigger an unrelated login flow, and observe the tokens delivered.
- Invariant to test: Provider tokens must only reach the flow that requested them.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: attach a listener, run an unrelated generateState flow and assert the listener is not invoked.
