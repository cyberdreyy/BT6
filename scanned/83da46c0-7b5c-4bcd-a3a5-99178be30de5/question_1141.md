# Q1141: 15 second race leaves the callback registered in EmbeddedWalletProxy.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through EmbeddedWalletProxy.invoke (postMessage target '*') that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from EmbeddedWalletProxy.invoke (postMessage target '*'), deliver the late reply and assert it is ignored.
