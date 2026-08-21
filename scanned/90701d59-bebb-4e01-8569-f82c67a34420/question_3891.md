# Q3891: ping doubles as a liveness oracle in EmbeddedWalletProxy.ts

## Question
ping() invokes privy:iframe:ready with a caller-controlled timeout; can an attacker use EmbeddedWalletProxy.invoke (postMessage target '*') to keep the ready state true while the iframe is actually serving a different session?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Flip the iframe session and observe the cached ready flag.
- Invariant to test: Readiness must be invalidated when the underlying wallet session changes.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: change the session and assert EmbeddedWalletProxy.invoke (postMessage target '*') re-verifies readiness.
