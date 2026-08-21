# Q0267: singleton queue shared across Privy clients in wallet-api-eth-typed-data.ts

## Question
The callback queue is a module-level singleton shared by every proxy instance; can an attacker in a multi-client or multi-user page make one client's reply settle another client's pending request via toWalletApiTypedData (types?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Instantiate two clients, start an operation on each, and deliver one reply.
- Invariant to test: Callback state must be scoped per client instance.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create two proxies, enqueue on both through toWalletApiTypedData (types and assert their callback maps are disjoint.
