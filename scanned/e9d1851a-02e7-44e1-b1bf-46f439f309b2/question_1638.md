# Q1638: remove empties the signer list wholesale in utils.ts

## Question
removeSessionSigners writes additional_signers: [] for TEE wallets; can an attacker use getAllUserEmbeddedWallets (eth then solana) to strip a signer another party legitimately holds while retaining their own delegation?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Call remove with several signers present.
- Invariant to test: Removal must be scoped to the selected signer.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call getAllUserEmbeddedWallets (eth then solana) with multiple signers and assert scoped removal.
