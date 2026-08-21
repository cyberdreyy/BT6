# Q0165: from address defaults to the wallet in getWalletPublicKeyFromTransaction.ts

## Question
handlePopulateTransaction and handleEstimateGas use `transaction.from ?? this._account.address` while the signature is produced by the wallet regardless; can an attacker set a from that differs from the signer so the populated nonce and gas describe a different account?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Send a transaction with a foreign from and compare the populated fields to the signing account.
- Invariant to test: Populated fields must be derived from the account that will actually sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign from to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert rejection or that population uses the signer address.
