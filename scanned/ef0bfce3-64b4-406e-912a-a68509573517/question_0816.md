# Q0816: bigint and undefined fields collapse the cache key in wallet-api-eth-transaction.ts

## Question
The cache key is built with JSON.stringify, which drops undefined values and functions; can an attacker craft two different payloads that produce the same key inside toWalletApiUnsignedEthTransaction?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Pass payloads differing only by an undefined field and observe the shared cache entry.
- Invariant to test: Cache keys must be injective over the payloads they represent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert toWalletApiUnsignedEthTransaction produces different keys for payloads differing only in undefined-valued fields.
