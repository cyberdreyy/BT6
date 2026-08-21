# Q0156: predictable global request ids in wallet-api-eth-transaction.ts

## Question
Request ids come from a module-level counter emitting id-0, id-1, ...; can an attacker predict the next id and pre-deliver a reply through provider.request({method:'eth_signTransaction', params:[tx]}) so their data settles the victim's next operation?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Count the ids issued so far, then post a reply for the next id before the real iframe answers.
- Invariant to test: Reply correlation must use unguessable, per-instance identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run two operations through toWalletApiUnsignedEthTransaction and assert the ids are not sequentially predictable.
