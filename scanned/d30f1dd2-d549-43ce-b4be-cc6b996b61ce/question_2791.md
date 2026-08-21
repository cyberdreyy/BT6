# Q2791: eth_sign and secp256k1_sign share a path in EmbeddedWalletProxy.ts

## Question
walletRpc maps eth_sign and secp256k1_sign to the same raw hash signing method; can an attacker use EmbeddedWalletProxy.invoke (postMessage target '*') to obtain a raw-hash signature over a value the user believed was a display message?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Submit a 32-byte hash-shaped string through the message path.
- Invariant to test: Raw hash signing must be visibly distinct from message signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EmbeddedWalletProxy.invoke (postMessage target '*') refuses raw-hash signing without an explicit raw-sign intent.
