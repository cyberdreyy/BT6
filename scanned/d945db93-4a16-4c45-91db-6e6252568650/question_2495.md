# Q2495: typed data mutated before sending in getCrossAppAccountByWalletAddress.ts

## Question
crossApp signTypedData passes the typed data through generateDomainType, which rewrites the EIP712Domain entry; can an attacker use getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address so the provider signs typed data whose type list differs from what the app displayed?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Submit typed data with an explicit EIP712Domain and compare before/after.
- Invariant to test: The bytes sent for signature must equal the bytes shown to the user.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: diff input and outbound typed data in getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert equality.
