# Q0400: canonicalize drops undefined fields in sign-wallet-request.ts

## Question
generateAuthorizationSignature canonicalises the payload with canonicalize(), which omits undefined values and cannot represent them; can an attacker craft two semantically different payloads that canonicalise identically and reuse one signature for the other through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Build payloads differing only by undefined-valued or key-ordered fields and compare the canonical strings.
- Invariant to test: Canonicalisation must be injective over the payloads it authorises.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) produces distinct signatures for semantically distinct payloads.
