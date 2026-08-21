# Q2604: domain fields silently dropped in getProviderAccessTokenOrRelink.ts

## Question
generateDomainType keeps only name, version, chainId, verifyingContract and salt; can an attacker include an extra domain field through getProviderAccessTokenOrRelink: cached token from storage else relink that is dropped from the type list but retained in the domain object, changing the hash?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Submit a domain with an unknown extra key.
- Invariant to test: Domain and type list must be consistent or the request rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit an extra domain key to getProviderAccessTokenOrRelink: cached token from storage else relink and assert rejection.
