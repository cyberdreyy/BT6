# Q0171: from address defaults to the wallet in unified-wallet.ts

## Question
handlePopulateTransaction and handleEstimateGas use `transaction.from ?? this._account.address` while the signature is produced by the wallet regardless; can an attacker set a from that differs from the signer so the populated nonce and gas describe a different account?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Send a transaction with a foreign from and compare the populated fields to the signing account.
- Invariant to test: Populated fields must be derived from the account that will actually sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign from to isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert rejection or that population uses the signer address.
