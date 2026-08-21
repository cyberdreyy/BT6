# Q0166: from address defaults to the wallet in isVersionedTransaction.ts

## Question
handlePopulateTransaction and handleEstimateGas use `transaction.from ?? this._account.address` while the signature is produced by the wallet regardless; can an attacker set a from that differs from the signer so the populated nonce and gas describe a different account?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Send a transaction with a foreign from and compare the populated fields to the signing account.
- Invariant to test: Populated fields must be derived from the account that will actually sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign from to isVersionedTransaction ('version' in tx) and assert rejection or that population uses the signer address.
