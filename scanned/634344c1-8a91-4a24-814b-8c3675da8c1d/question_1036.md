# Q1036: waitForReady floods pings for 15 seconds in wallet-api-eth-transaction.ts

## Question
waitForReady loops 100 times at 150ms firing privy:iframe:ready invocations, each enqueuing a callback; can an attacker use toWalletApiUnsignedEthTransaction to fill the shared queue with callbacks that later collide with real operation ids?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Hold the iframe unready and count the enqueued callbacks left behind.
- Invariant to test: Readiness probing must not leave stale callbacks in the shared queue.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run toWalletApiUnsignedEthTransaction against an unready iframe and assert the queue is empty afterwards.
