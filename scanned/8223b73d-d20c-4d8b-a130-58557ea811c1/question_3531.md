# Q3531: oauth_tokens emitted to any listener in FarcasterV2Api.ts

## Question
Provider tokens from FarcasterV2Api.initializeAuth are emitted through the session 'oauth_tokens_granted' event to every registered listener; can an attacker register or keep a listener that receives another flow's provider tokens?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Attach a listener, trigger an unrelated login flow, and observe the tokens delivered.
- Invariant to test: Provider tokens must only reach the flow that requested them.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: attach a listener, run an unrelated FarcasterV2Api.initializeAuth flow and assert the listener is not invoked.
