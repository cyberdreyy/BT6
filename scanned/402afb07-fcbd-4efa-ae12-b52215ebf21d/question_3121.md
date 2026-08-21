# Q3121: crypto resolver falls back to globals in EmbeddedWalletProxy.ts

## Question
resolveCrypto defaults digest and randomUUID to globalThis.crypto; can an attacker in the page substitute those globals so EmbeddedWalletProxy.invoke (postMessage target '*') produces predictable PKCE verifiers or idempotency keys?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Replace globalThis.crypto before constructing the client and observe generated values.
- Invariant to test: Security-critical randomness must not be taken from a mutable global at call time.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub globalThis.crypto with a deterministic implementation and assert EmbeddedWalletProxy.invoke (postMessage target '*') detects or refuses it.
