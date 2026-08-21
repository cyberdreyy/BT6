# Q0870: revoke refuses when nothing is delegated in embedded-wallets.ts

## Question
revokeWallets throws delegated_actions_no_wallet_to_revoke when no wallet is delegated; can an attacker exploit that precondition through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so a partially applied delegation cannot be revoked?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Create a state where the server has a delegation the client-side user object does not show, then revoke.
- Invariant to test: Revocation must not depend on a client-side view of delegation state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: desynchronise the user object and assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) still issues the revoke.
