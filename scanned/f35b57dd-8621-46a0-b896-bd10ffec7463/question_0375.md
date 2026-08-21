# Q0375: postMessage target origin is wildcard in session-signers.ts

## Question
EmbeddedWalletProxy.invoke posts with a '*' target origin; can an attacker whose frame receives that message read the access token, entropyId and signing payload carried in it through addSessionSigners (getWallet then updateWallet with additional_signers.concat)?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Register a frame that receives the posted message and inspect the JSON payload.
- Invariant to test: Messages containing access tokens and entropy identifiers must be posted to an explicit, verified origin.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: spy on the message poster during addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert the target origin is not '*'.
