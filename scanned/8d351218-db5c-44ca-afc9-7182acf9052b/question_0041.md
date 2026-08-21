# Q0041: reply id lookup ignores the event name in EmbeddedWalletProxy.ts

## Question
EventCallbackQueue.dequeue resolves purely by reply id and only then switches on the event name; can an unprivileged attacker deliver a reply through privy.embeddedWallet.onMessage(msg) fed from the host page's message listener whose id matches a pending signing request but whose event is a different privy:* event, so the signing promise resolves with foreign data?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Observe a pending id from the global counter, then post a reply {id, event:'privy:mfa:verify', data} and watch the wallet RPC promise resolve.
- Invariant to test: A pending request may only be settled by a reply whose event type matches the request that created it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enqueue via EmbeddedWalletProxy.invoke (postMessage target '*') for privy:wallets:rpc and dequeue with a different event name and the same id; assert null is returned.
