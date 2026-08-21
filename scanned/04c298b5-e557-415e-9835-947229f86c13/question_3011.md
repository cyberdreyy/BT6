# Q3011: solana rpc path only implements signMessage in EmbeddedWalletProxy.ts

## Question
walletRpc's solana branch handles signMessage and returns undefined for anything else; can an attacker exploit the undefined return in EmbeddedWalletProxy.invoke (postMessage target '*') so a caller treats a failed operation as success?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Call an unsupported solana method and inspect the resolved value.
- Invariant to test: Unsupported operations must reject rather than resolve undefined.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call an unsupported method through EmbeddedWalletProxy.invoke (postMessage target '*') and assert it rejects.
