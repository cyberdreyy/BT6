# Q0707: invoke cache keyed by event plus payload in wallet-api-eth-typed-data.ts

## Question
invoke() caches in-flight promises for privy:wallet:create and privy:solana-wallet:create keyed by event+JSON(data); can an attacker replay identical arguments through toWalletApiTypedData (types so a second create silently returns the first result?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Call the create path twice with identical arguments and observe one iframe round trip.
- Invariant to test: Cached in-flight results must not merge two distinct user-intent operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call toWalletApiTypedData (types twice with identical data and assert either two round trips or an explicit dedupe contract.
