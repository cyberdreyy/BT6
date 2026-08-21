# Q1581: first-wallet fallback for entropy in EmbeddedWalletProxy.ts

## Question
getEntropyDetailsFromUser falls back to the first ethereum wallet, then the first solana wallet; can an attacker with multiple linked wallets cause EmbeddedWalletProxy.invoke (postMessage target '*') to derive entropy from a wallet other than the one being signed with?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Sign with a wallet at index 1 and inspect the entropy identity used.
- Invariant to test: Entropy identity must correspond to the exact signing account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call EmbeddedWalletProxy.invoke (postMessage target '*') with a non-zero wallet_index account and assert the entropy matches that account.
