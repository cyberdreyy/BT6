# Q2740: errors distinguish existence of accounts in embedded-wallets.ts

## Question
delegated_actions_wallet_not_found is returned for addresses not on the account; can an attacker use isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) to probe which addresses belong to the current user?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Submit candidate addresses and compare error codes.
- Invariant to test: Error responses must not confirm account membership beyond what the caller already knows.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) returns a uniform error for unknown addresses.
