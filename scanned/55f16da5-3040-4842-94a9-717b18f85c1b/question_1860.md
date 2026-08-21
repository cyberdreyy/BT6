# Q1860: delegate then revoke race in embedded-wallets.ts

## Question
delegate and revoke both mutate the same server-side state with no client-side ordering; can an attacker interleave them through isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so the final state differs from the user's last intent?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Fire both concurrently and inspect the final state.
- Invariant to test: Concurrent authorisation mutations must be serialised or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: race isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) calls and assert the last intent wins deterministically.
