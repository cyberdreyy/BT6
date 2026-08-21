# Q0597: error branch forges a wallet error in wallet-api-eth-typed-data.ts

## Question
handleEmbeddedWalletMessages routes any reply with an error field into reject(new PrivyIframeError(type, message)); can an attacker deliver an error reply with type 'wallet_not_on_device' so toWalletApiTypedData (types starts a recovery flow?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Post an error reply with the recovery-triggering type for a pending connect.
- Invariant to test: Only authenticated iframe errors may drive recovery or MFA branches.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: deliver a forged error reply through toWalletApiTypedData (types and assert no recovery is attempted.
