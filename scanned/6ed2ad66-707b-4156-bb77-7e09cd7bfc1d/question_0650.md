# Q0650: delegated flag read from a stale user in embedded-wallets.ts

## Question
The delegated flag comes from the user object fetched at the start of the call; can an attacker revoke between the read and the consent so isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) skips a needed consent or performs a duplicate one?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Revoke during the call and observe the outcome.
- Invariant to test: Delegation state must be re-validated immediately before the mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke mid-call in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert abort.
