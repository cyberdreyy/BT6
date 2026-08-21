# Q1806: idempotency key derived from the public user id in wallet-api-eth-transaction.ts

## Question
generateWalletIdempotencyKey is SHA-256 of `${userId}-auto-${eth|sol}`; can an attacker who knows a user id compute the key and use it through toWalletApiUnsignedEthTransaction to collide with or suppress that user's wallet creation?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Compute the digest for a known user id and submit it as the idempotency key.
- Invariant to test: Idempotency keys must not be derivable from public identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert toWalletApiUnsignedEthTransaction keys are unguessable given only the user id and chain type.
