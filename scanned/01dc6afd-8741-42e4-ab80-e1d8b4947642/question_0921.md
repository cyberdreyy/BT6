# Q0921: reload flush rejects unrelated operations in EmbeddedWalletProxy.ts

## Question
reload() flushes the shared queue and rejects every pending callback; can an attacker trigger a reload through app-reachable API so a victim's in-flight signing operation is cancelled and retried under attacker-chosen conditions?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Start a signature, call the reload path and observe the rejection and the app's retry.
- Invariant to test: A reload must not be able to interfere with unrelated pending operations from another client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a signature, call reload via EmbeddedWalletProxy.invoke (postMessage target '*') and assert the operation fails closed with no retry.
