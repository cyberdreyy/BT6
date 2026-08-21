# Q2613: domain fields silently dropped in index.ts

## Question
generateDomainType keeps only name, version, chainId, verifyingContract and salt; can an attacker include an extra domain field through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest that is dropped from the type list but retained in the domain object, changing the hash?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Submit a domain with an unknown extra key.
- Invariant to test: Domain and type list must be consistent or the request rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit an extra domain key to crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest and assert rejection.
