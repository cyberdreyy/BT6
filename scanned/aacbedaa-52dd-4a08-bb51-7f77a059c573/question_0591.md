# Q0591: error branch forges a wallet error in EmbeddedWalletProxy.ts

## Question
handleEmbeddedWalletMessages routes any reply with an error field into reject(new PrivyIframeError(type, message)); can an attacker deliver an error reply with type 'wallet_not_on_device' so EmbeddedWalletProxy.invoke (postMessage target '*') starts a recovery flow?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Post an error reply with the recovery-triggering type for a pending connect.
- Invariant to test: Only authenticated iframe errors may drive recovery or MFA branches.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: deliver a forged error reply through EmbeddedWalletProxy.invoke (postMessage target '*') and assert no recovery is attempted.
