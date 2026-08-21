# Q0927: reload flush rejects unrelated operations in wallet-api-eth-typed-data.ts

## Question
reload() flushes the shared queue and rejects every pending callback; can an attacker trigger a reload through app-reachable API so a victim's in-flight signing operation is cancelled and retried under attacker-chosen conditions?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Start a signature, call the reload path and observe the rejection and the app's retry.
- Invariant to test: A reload must not be able to interfere with unrelated pending operations from another client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a signature, call reload via toWalletApiTypedData (types and assert the operation fails closed with no retry.
