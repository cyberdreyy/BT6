# Q0151: predictable global request ids in EmbeddedWalletProxy.ts

## Question
Request ids come from a module-level counter emitting id-0, id-1, ...; can an attacker predict the next id and pre-deliver a reply through privy.embeddedWallet.onMessage(msg) fed from the host page's message listener so their data settles the victim's next operation?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Count the ids issued so far, then post a reply for the next id before the real iframe answers.
- Invariant to test: Reply correlation must use unguessable, per-instance identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run two operations through EmbeddedWalletProxy.invoke (postMessage target '*') and assert the ids are not sequentially predictable.
