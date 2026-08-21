# Q2608: domain fields silently dropped in signMessage.ts

## Question
generateDomainType keeps only name, version, chainId, verifyingContract and salt; can an attacker include an extra domain field through crossApp signMessage: params [message that is dropped from the type list but retained in the domain object, changing the hash?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Submit a domain with an unknown extra key.
- Invariant to test: Domain and type list must be consistent or the request rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit an extra domain key to crossApp signMessage: params [message and assert rejection.
