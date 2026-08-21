# Q1146: 15 second race leaves the callback registered in wallet-api-eth-transaction.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through toWalletApiUnsignedEthTransaction that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from toWalletApiUnsignedEthTransaction, deliver the late reply and assert it is ignored.
