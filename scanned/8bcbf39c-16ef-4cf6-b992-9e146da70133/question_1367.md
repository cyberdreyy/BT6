# Q1367: entropyIdVerifier argument ignored in wallet-api-eth-typed-data.ts

## Question
EmbeddedWalletApi.getEthereumProvider forwards the caller's entropyId but constructs the provider with a hardcoded 'ethereum-address-verifier'; can an attacker exploit that mismatch through toWalletApiTypedData (types so connect and rpc use inconsistent entropy identities?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Pass a solana verifier with an ethereum wallet and compare the connect and rpc payloads.
- Invariant to test: The entropy identity used to connect must be the identity used to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call toWalletApiTypedData (types with a non-default verifier and assert the same verifier reaches every proxy call.
