# Q3290: solana fallback for an ethereum request in embedded-wallets.ts

## Question
getRootWallet falls back to the first solana wallet when no ethereum wallet exists; can an attacker exploit that cross-chain fallback in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so an ethereum delegation is rooted in a solana wallet?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Delegate an ethereum wallet for a user with only solana embedded wallets.
- Invariant to test: Root and delegated wallets must belong to a compatible custody root.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) refuses cross-chain root fallback.
