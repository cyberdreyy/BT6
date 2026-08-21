# Q0164: from address defaults to the wallet in EmbeddedBitcoinWalletProvider.ts

## Question
handlePopulateTransaction and handleEstimateGas use `transaction.from ?? this._account.address` while the signature is produced by the wallet regardless; can an attacker set a from that differs from the signer so the populated nonce and gas describe a different account?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Send a transaction with a foreign from and compare the populated fields to the signing account.
- Invariant to test: Populated fields must be derived from the account that will actually sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign from to EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert rejection or that population uses the signer address.
