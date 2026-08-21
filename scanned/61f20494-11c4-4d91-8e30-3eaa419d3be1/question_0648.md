# Q0648: delegated flag read from a stale user in utils.ts

## Question
The delegated flag comes from the user object fetched at the start of the call; can an attacker revoke between the read and the consent so getAllUserEmbeddedWallets (eth then solana) skips a needed consent or performs a duplicate one?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Revoke during the call and observe the outcome.
- Invariant to test: Delegation state must be re-validated immediately before the mutation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke mid-call in getAllUserEmbeddedWallets (eth then solana) and assert abort.
