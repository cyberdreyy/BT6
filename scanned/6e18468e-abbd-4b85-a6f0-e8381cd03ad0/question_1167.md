# Q1167: version field is a constant in get-wallet.ts

## Question
Every envelope sets version: 1; can an attacker exploit the absence of a nonce or request id so two identical operations from getWallet(): WalletGet by wallet_id produce byte-identical signatures that are interchangeable?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Issue the same operation twice and compare signatures.
- Invariant to test: Envelopes must include a unique per-request nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: issue the same getWallet(): WalletGet by wallet_id operation twice and assert the signatures differ.
