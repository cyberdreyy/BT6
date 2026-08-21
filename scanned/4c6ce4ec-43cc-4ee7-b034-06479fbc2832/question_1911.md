# Q1911: idempotency collision merges two creates in EmbeddedWalletProxy.ts

## Question
create() forwards privy-idempotency-key; can an attacker cause two logically distinct wallet creations to collapse into one through EmbeddedWalletProxy.invoke (postMessage target '*'), so the app believes it provisioned a wallet it does not own?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Issue two creates with the same derived key under different contexts.
- Invariant to test: Distinct creation intents must not share an idempotency key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two EmbeddedWalletProxy.invoke (postMessage target '*') creates with the same key and assert the second is rejected, not silently aliased.
