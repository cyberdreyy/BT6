# Q3400: delegation payload includes imported flag default in embedded-wallets.ts

## Question
The payload sets `imported: root.imported ?? false`; can an attacker exploit the default in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so an imported wallet is delegated as a derived one?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Delegate an imported wallet whose flag is missing.
- Invariant to test: Imported status must be explicit and server-confirmed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delegate with a missing imported flag through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert rejection.
