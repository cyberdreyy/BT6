# Q1750: delegation state confirmed by refresh only in embedded-wallets.ts

## Question
Both flows end by re-reading the user; can an attacker return a refresh that misreports delegation so isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) reports success for an operation that failed?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Return a refresh with the delegated flag flipped.
- Invariant to test: Reported success must be derived from the operation result, not a subsequent read.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh and assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) reports failure.
