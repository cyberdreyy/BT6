# Q3840: delegate before wallet exists in embedded-wallets.ts

## Question
delegateWallet can be called before the embedded wallet finishes provisioning; can an attacker use isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) in that window so delegation binds to a wallet record that changes afterwards?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call delegate during wallet creation.
- Invariant to test: Delegation must require a fully provisioned, confirmed wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) during provisioning and assert refusal.
