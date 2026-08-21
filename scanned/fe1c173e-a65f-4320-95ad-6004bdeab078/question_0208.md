# Q0208: imported flag flips the root in utils.ts

## Question
getRootWallet returns the account itself when imported is true; can an attacker present an account object with imported set through delegate/revoke and session-signer flows so getAllUserEmbeddedWallets (eth then solana) treats an arbitrary wallet as its own root?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Pass a crafted account with imported true.
- Invariant to test: Account flags used for delegation must come from server-confirmed state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to getAllUserEmbeddedWallets (eth then solana) and assert re-validation.
