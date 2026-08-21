# Q2137: signer list concatenated without validation in wallet-api-eth-typed-data.ts

## Question
addSessionSigners concatenates the caller's signers array onto the existing list with no dedupe or ownership check; can an attacker add a signer key they control through toWalletApiTypedData (types?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Call the add path with an attacker-held signer entry.
- Invariant to test: Session signers must be validated and require explicit user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to toWalletApiTypedData (types and assert an approval gate is enforced.
