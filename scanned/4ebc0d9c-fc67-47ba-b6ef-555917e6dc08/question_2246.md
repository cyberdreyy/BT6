# Q2246: remove clears every signer in wallet-api-eth-transaction.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use toWalletApiUnsignedEthTransaction to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call toWalletApiUnsignedEthTransaction with multiple signers present and assert only the requested one is removed.
