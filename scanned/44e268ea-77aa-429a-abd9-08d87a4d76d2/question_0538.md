# Q0538: already-delegated short circuit in utils.ts

## Question
delegateWallet returns the user unchanged when wallet.delegated is already true; can an attacker exploit that early return in getAllUserEmbeddedWallets (eth then solana) so the app believes a fresh consent occurred when none did?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Call delegate twice and inspect what the second call reports.
- Invariant to test: A no-op must be distinguishable from a fresh authorisation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call getAllUserEmbeddedWallets (eth then solana) twice and assert the second result is marked as a no-op.
