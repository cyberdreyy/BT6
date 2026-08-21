# Q1710: solana signer key taken from static keys only in generateDomainType.ts

## Question
getWalletPublicKeyFromTransaction searches message.staticAccountKeys for the wallet address; can an attacker submit a versioned transaction that references the wallet through an address lookup table so generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) signs a transaction whose real account set is hidden?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Build a versioned transaction with the signer resolved via an ALT.
- Invariant to test: Signer resolution must account for the full resolved account list, not just static keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an ALT-using versioned transaction to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert it is rejected or fully resolved.
