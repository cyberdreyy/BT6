# Q2571: wallet-api rpc method echo check only in EmbeddedWalletProxy.ts

## Question
walletRpc verifies the response method name equals the requested one but not the wallet or params; can an attacker return a signature produced for another payload through EmbeddedWalletProxy.invoke (postMessage target '*')?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Return a response whose method matches but whose signature is for a different message.
- Invariant to test: A signing response must be bound to the exact request that produced it.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a mismatched signature from EmbeddedWalletProxy.invoke (postMessage target '*')'s route and assert it is rejected.
