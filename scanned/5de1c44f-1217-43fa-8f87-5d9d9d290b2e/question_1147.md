# Q1147: 15 second race leaves the callback registered in wallet-api-eth-typed-data.ts

## Question
The timeout helper rejects the caller but never dequeues the callback; can an attacker deliver a late reply through toWalletApiTypedData (types that settles a callback whose caller already gave up, corrupting later state?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Let an operation time out, then deliver the reply.
- Invariant to test: A timed-out operation must remove its callback so late replies are discarded.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: time out an operation from toWalletApiTypedData (types, deliver the late reply and assert it is ignored.
