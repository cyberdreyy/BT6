# Q2298: revoke route takes no body in utils.ts

## Question
DelegatedWalletsApi.revoke posts an empty body; can an attacker trigger getAllUserEmbeddedWallets (eth then solana) repeatedly so a user's re-established delegation is immediately removed each time, keeping them dependent on a flow the attacker controls?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Call revoke repeatedly around the user's delegate calls.
- Invariant to test: Revocation must be an authenticated, user-initiated action with a clear audit result.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: interleave repeated getAllUserEmbeddedWallets (eth then solana) calls with delegation and assert user intent prevails.
