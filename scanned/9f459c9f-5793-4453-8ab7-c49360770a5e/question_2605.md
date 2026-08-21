# Q2605: domain fields silently dropped in getCrossAppAccountByWalletAddress.ts

## Question
generateDomainType keeps only name, version, chainId, verifyingContract and salt; can an attacker include an extra domain field through getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address that is dropped from the type list but retained in the domain object, changing the hash?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Submit a domain with an unknown extra key.
- Invariant to test: Domain and type list must be consistent or the request rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit an extra domain key to getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address and assert rejection.
