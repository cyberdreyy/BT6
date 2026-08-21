# Q2357: delegated fallback path for on-device wallets in wallet-api-eth-typed-data.ts

## Question
addSessionSigners falls back to delegateWallets when the wallet is not TEE-backed; can an attacker use toWalletApiTypedData (types to convert a session-signer request into a full delegation the user never approved?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Call the add path with an on-device wallet and an empty signers array.
- Invariant to test: A session-signer request must not silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run toWalletApiTypedData (types on an on-device wallet and assert the consent prompt describes delegation.
