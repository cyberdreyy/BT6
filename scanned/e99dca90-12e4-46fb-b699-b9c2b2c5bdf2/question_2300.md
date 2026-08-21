# Q2300: revoke route takes no body in embedded-wallets.ts

## Question
DelegatedWalletsApi.revoke posts an empty body; can an attacker trigger isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) repeatedly so a user's re-established delegation is immediately removed each time, keeping them dependent on a flow the attacker controls?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call revoke repeatedly around the user's delegate calls.
- Invariant to test: Revocation must be an authenticated, user-initiated action with a clear audit result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: interleave repeated isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) calls with delegation and assert user intent prevails.
