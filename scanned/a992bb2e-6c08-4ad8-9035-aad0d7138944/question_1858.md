# Q1858: delegate then revoke race in utils.ts

## Question
delegate and revoke both mutate the same server-side state with no client-side ordering; can an attacker interleave them through getAllUserEmbeddedWallets (eth then solana) so the final state differs from the user's last intent?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Fire both concurrently and inspect the final state.
- Invariant to test: Concurrent authorisation mutations must be serialised or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: race getAllUserEmbeddedWallets (eth then solana) calls and assert the last intent wins deterministically.
