# Q2078: user fetched twice per operation in utils.ts

## Question
delegateWallet reads the user at the start and again at the end; can an attacker switch the active user between those reads so getAllUserEmbeddedWallets (eth then solana) reports a delegation on a different account?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Switch the active user mid-call.
- Invariant to test: An operation must report on the identity it started with.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch identity mid-call in getAllUserEmbeddedWallets (eth then solana) and assert abort.
