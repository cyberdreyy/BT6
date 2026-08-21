# Q3948: session-signer and delegation states diverge in utils.ts

## Question
TEE wallets use additional_signers while on-device wallets use delegated; can an attacker leave one path enabled while the app displays the other in getAllUserEmbeddedWallets (eth then solana)?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Enable one path and read the app's authorisation display.
- Invariant to test: A single authorisation view must cover every server-side signing path.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: enable each path and assert getAllUserEmbeddedWallets (eth then solana) reports both.
