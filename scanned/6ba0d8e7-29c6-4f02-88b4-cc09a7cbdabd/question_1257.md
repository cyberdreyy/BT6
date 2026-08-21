# Q1257: access token embedded in every proxy payload in wallet-api-eth-typed-data.ts

## Question
Every proxy call carries accessToken alongside entropyId and entropyIdVerifier; can an attacker observe or replay one of those payloads through toWalletApiTypedData (types to authorise a wallet operation later?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Capture a posted payload and replay it into the same interface.
- Invariant to test: Wallet operation payloads must not be replayable outside their original request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: replay a captured payload into toWalletApiTypedData (types and assert it is rejected.
