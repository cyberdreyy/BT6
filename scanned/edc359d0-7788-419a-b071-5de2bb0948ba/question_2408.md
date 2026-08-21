# Q2408: embedded classification decides delegability in utils.ts

## Question
isEmbeddedWalletAccount requires type wallet, wallet_client_type privy and connector_type embedded; can an attacker present an external wallet with those fields through getAllUserEmbeddedWallets (eth then solana) so it is treated as delegable?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Pass an account with spoofed classification fields.
- Invariant to test: Wallet classification must come from server-confirmed records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass spoofed classification fields to getAllUserEmbeddedWallets (eth then solana) and assert re-validation.
