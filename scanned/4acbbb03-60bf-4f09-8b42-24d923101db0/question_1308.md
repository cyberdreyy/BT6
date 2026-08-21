# Q1308: session-signer add falls back to delegation in utils.ts

## Question
addSessionSigners delegates instead when the wallet is not TEE-backed; can an attacker use getAllUserEmbeddedWallets (eth then solana) so a request the app described as adding a server signer instead grants a full delegation?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Call the add path with an on-device wallet.
- Invariant to test: A session-signer request must never silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call getAllUserEmbeddedWallets (eth then solana) on an on-device wallet and assert the consent text matches the action.
