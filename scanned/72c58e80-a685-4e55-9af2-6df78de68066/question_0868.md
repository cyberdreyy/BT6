# Q0868: revoke refuses when nothing is delegated in utils.ts

## Question
revokeWallets throws delegated_actions_no_wallet_to_revoke when no wallet is delegated; can an attacker exploit that precondition through getAllUserEmbeddedWallets (eth then solana) so a partially applied delegation cannot be revoked?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Create a state where the server has a delegation the client-side user object does not show, then revoke.
- Invariant to test: Revocation must not depend on a client-side view of delegation state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: desynchronise the user object and assert getAllUserEmbeddedWallets (eth then solana) still issues the revoke.
