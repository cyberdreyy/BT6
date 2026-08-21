# Q1528: empty signers array is meaningful in utils.ts

## Question
addSessionSigners requires a non-empty array for TEE wallets but requires an empty one for on-device wallets; can an attacker exploit that inversion in getAllUserEmbeddedWallets (eth then solana) so the wrong branch executes for the wallet type?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Call with an empty array for a TEE wallet and a populated one for an on-device wallet.
- Invariant to test: Branch selection and argument validation must be consistent per wallet type.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cross wallet type and signers shape in getAllUserEmbeddedWallets (eth then solana) and assert clear errors.
