# Q2247: remove clears every signer in wallet-api-eth-typed-data.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use toWalletApiTypedData (types to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call toWalletApiTypedData (types with multiple signers present and assert only the requested one is removed.
