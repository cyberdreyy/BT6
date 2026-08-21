# Q0540: already-delegated short circuit in embedded-wallets.ts

## Question
delegateWallet returns the user unchanged when wallet.delegated is already true; can an attacker exploit that early return in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so the app believes a fresh consent occurred when none did?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call delegate twice and inspect what the second call reports.
- Invariant to test: A no-op must be distinguishable from a fresh authorisation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) twice and assert the second result is marked as a no-op.
