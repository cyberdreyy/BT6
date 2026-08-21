# Q1031: waitForReady floods pings for 15 seconds in EmbeddedWalletProxy.ts

## Question
waitForReady loops 100 times at 150ms firing privy:iframe:ready invocations, each enqueuing a callback; can an attacker use EmbeddedWalletProxy.invoke (postMessage target '*') to fill the shared queue with callbacks that later collide with real operation ids?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Hold the iframe unready and count the enqueued callbacks left behind.
- Invariant to test: Readiness probing must not leave stale callbacks in the shared queue.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run EmbeddedWalletProxy.invoke (postMessage target '*') against an unready iframe and assert the queue is empty afterwards.
