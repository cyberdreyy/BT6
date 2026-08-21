# Q0706: invoke cache keyed by event plus payload in wallet-api-eth-transaction.ts

## Question
invoke() caches in-flight promises for privy:wallet:create and privy:solana-wallet:create keyed by event+JSON(data); can an attacker replay identical arguments through toWalletApiUnsignedEthTransaction so a second create silently returns the first result?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Call the create path twice with identical arguments and observe one iframe round trip.
- Invariant to test: Cached in-flight results must not merge two distinct user-intent operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call toWalletApiUnsignedEthTransaction twice with identical data and assert either two round trips or an explicit dedupe contract.
