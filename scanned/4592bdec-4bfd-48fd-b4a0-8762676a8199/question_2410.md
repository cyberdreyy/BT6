# Q2410: embedded classification decides delegability in embedded-wallets.ts

## Question
isEmbeddedWalletAccount requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present an external wallet with those fields through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so it is treated as delegable?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Wallet classification must come from server-confirmed records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed classification fields to isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert re-validation.
