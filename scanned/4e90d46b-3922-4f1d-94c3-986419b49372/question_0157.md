# Q0157: predictable global request ids in wallet-api-eth-typed-data.ts

## Question
Request ids come from a module-level counter emitting id-0, id-1, ...; can an attacker predict the next id and pre-deliver a reply through provider.request({method:'eth_signTypedData_v4', params:[address, typedData]}) so their data settles the victim's next operation?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Count the ids issued so far, then post a reply for the next id before the real iframe answers.
- Invariant to test: Reply correlation must use unguessable, per-instance identifiers.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: run two operations through toWalletApiTypedData (types and assert the ids are not sequentially predictable.
