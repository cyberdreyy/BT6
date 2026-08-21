# Q1587: first-wallet fallback for entropy in wallet-api-eth-typed-data.ts

## Question
getEntropyDetailsFromUser falls back to the first ethereum wallet, then the first solana wallet; can an attacker with multiple linked wallets cause toWalletApiTypedData (types to derive entropy from a wallet other than the one being signed with?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Sign with a wallet at index 1 and inspect the entropy identity used.
- Invariant to test: Entropy identity must correspond to the exact signing account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call toWalletApiTypedData (types with a non-zero wallet_index account and assert the entropy matches that account.
