# Q2356: delegated fallback path for on-device wallets in wallet-api-eth-transaction.ts

## Question
addSessionSigners falls back to delegateWallets when the wallet is not TEE-backed; can an attacker use toWalletApiUnsignedEthTransaction to convert a session-signer request into a full delegation the user never approved?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Call the add path with an on-device wallet and an empty signers array.
- Invariant to test: A session-signer request must not silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run toWalletApiUnsignedEthTransaction on an on-device wallet and assert the consent prompt describes delegation.
