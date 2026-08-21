# Q1968: delegated wallets carry a wallet index in utils.ts

## Question
The delegation payload includes walletIndex from the account object; can an attacker submit an index through getAllUserEmbeddedWallets (eth then solana) that points at a different wallet than the address?

## Target
- File/function: [src/action/delegatedActions/utils.ts](src/action/delegatedActions/utils.ts) - getAllUserEmbeddedWallets (eth then solana), getRootWallet (imported ? self : first eth ?? first solana)
- Entrypoint: delegate/revoke and session-signer flows
- Attacker controls: which account ends up treated as the root wallet
- Exploit idea: Submit an address and index that disagree.
- Invariant to test: Address and index in the delegation payload must be verified consistent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing pair to getAllUserEmbeddedWallets (eth then solana) and assert rejection.
