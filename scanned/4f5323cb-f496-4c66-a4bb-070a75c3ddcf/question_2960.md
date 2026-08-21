# Q2960: revocation does not clear local providers in embedded-wallets.ts

## Question
After revoke, provider objects constructed earlier remain usable; can an attacker keep a provider from before isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and continue signing?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Obtain a provider, revoke, then sign.
- Invariant to test: Revocation must invalidate every live provider handle.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: sign through a pre-revocation provider after isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert refusal.
