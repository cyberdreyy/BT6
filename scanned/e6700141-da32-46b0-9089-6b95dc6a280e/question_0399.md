# Q0399: canonicalize drops undefined fields in types.ts

## Question
generateAuthorizationSignature canonicalises the payload with canonicalize(), which omits undefined values and cannot represent them; can an attacker craft two semantically different payloads that canonicalise identically and reuse one signature for the other through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry')?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Build payloads differing only by undefined-valued or key-ordered fields and compare the canonical strings.
- Invariant to test: Canonicalisation must be injective over the payloads it authorises.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') produces distinct signatures for semantically distinct payloads.
