# Q0266: singleton queue shared across Privy clients in wallet-api-eth-transaction.ts

## Question
The callback queue is a module-level singleton shared by every proxy instance; can an attacker in a multi-client or multi-user page make one client's reply settle another client's pending request via toWalletApiUnsignedEthTransaction?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Instantiate two clients, start an operation on each, and deliver one reply.
- Invariant to test: Callback state must be scoped per client instance.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create two proxies, enqueue on both through toWalletApiUnsignedEthTransaction and assert their callback maps are disjoint.
