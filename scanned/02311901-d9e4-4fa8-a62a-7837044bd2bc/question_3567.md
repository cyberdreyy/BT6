# Q3567: solana create takes an ethereum account argument in wallet-api-eth-typed-data.ts

## Question
createSolana accepts an ethereumAccount whose provider is loaded first; can an attacker pass a foreign ethereum account through toWalletApiTypedData (types so entropy from another wallet is used for the new Solana wallet?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-typed-data.ts](src/embedded/stack/wallet-api-eth-typed-data.ts) - toWalletApiTypedData (types, primary_type via String(), domain, message pass-through)
- Entrypoint: provider.request({method:'eth_signTypedData_v4', params:[address, typedData]})
- Attacker controls: the entire typed-data object, including domain.chainId/verifyingContract and primaryType
- Exploit idea: Call createSolana with an ethereum account object that is not the user's.
- Invariant to test: Cross-chain wallet derivation must use only the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign ethereum account to toWalletApiTypedData (types and assert rejection.
