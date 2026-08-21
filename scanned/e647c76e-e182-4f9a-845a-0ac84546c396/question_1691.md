# Q1691: imported wallets bypass the fallback in EmbeddedWalletProxy.ts

## Question
getEntropyDetailsFromUser returns the signing account directly when imported is set; can an attacker mark an account object as imported so EmbeddedWalletProxy.invoke (postMessage target '*') derives entropy from an account of their choosing?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Pass a hand-built account with imported true.
- Invariant to test: Account flags used for entropy selection must come from server-confirmed data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to EmbeddedWalletProxy.invoke (postMessage target '*') and assert re-validation against the session user.
