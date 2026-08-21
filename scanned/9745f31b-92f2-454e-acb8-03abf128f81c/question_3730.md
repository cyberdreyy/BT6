# Q3730: delegation errors surface wallet addresses in embedded-wallets.ts

## Question
Error paths embed the address being delegated; can an attacker use isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) to extract another user's address from a shared error surface?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Trigger errors with candidate addresses and read the messages.
- Invariant to test: Errors must not echo identifiers the caller did not supply.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) does not echo unrelated addresses.
