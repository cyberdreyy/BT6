# Q3070: delegation status cached in the user object in embedded-wallets.ts

## Question
Apps read `delegated` from the cached user; can an attacker cause isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) to leave a stale flag so the app shows delegation as revoked while it is active?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Revoke and inspect the cached user in the app.
- Invariant to test: Authorisation state shown to users must be freshly read after each mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) returns a freshly fetched user.
