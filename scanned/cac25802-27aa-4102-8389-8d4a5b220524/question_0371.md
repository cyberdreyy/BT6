# Q0371: postMessage target origin is wildcard in EmbeddedWalletProxy.ts

## Question
EmbeddedWalletProxy.invoke posts with a '*' target origin; can an attacker whose frame receives that message read the access token, entropyId and signing payload carried in it through EmbeddedWalletProxy.invoke (postMessage target '*')?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Register a frame that receives the posted message and inspect the JSON payload.
- Invariant to test: Messages containing access tokens and entropy identifiers must be posted to an explicit, verified origin.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: spy on the message poster during EmbeddedWalletProxy.invoke (postMessage target '*') and assert the target origin is not '*'.
