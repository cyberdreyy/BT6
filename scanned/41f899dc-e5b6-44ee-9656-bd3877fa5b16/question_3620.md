# Q3620: revoke result not verified against server in embedded-wallets.ts

## Question
revokeWallets returns the refreshed user without asserting that no delegation remains; can an attacker leave a residual delegation that isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) reports as revoked?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Return a refresh that still shows a delegated wallet.
- Invariant to test: Revocation must be verified in the result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return a contradicting refresh to isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert failure is reported.
