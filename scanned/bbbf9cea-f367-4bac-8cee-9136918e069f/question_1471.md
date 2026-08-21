# Q1471: entropyId is just the wallet address in EmbeddedWalletProxy.ts

## Question
getEntropyDetailsFromAccount uses the account address as the entropyId; can an attacker pass an address they merely know through EmbeddedWalletProxy.invoke (postMessage target '*') and cause the iframe to load or recover the wrong wallet?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Call the provider path with a foreign address as entropyId.
- Invariant to test: Entropy identifiers must be validated against the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign address into EmbeddedWalletProxy.invoke (postMessage target '*') and assert it is rejected before the proxy call.
