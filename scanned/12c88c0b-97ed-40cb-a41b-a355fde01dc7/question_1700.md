# Q1700: imported wallets bypass the fallback in encodings.ts

## Question
getEntropyDetailsFromUser returns the signing account directly when imported is set; can an attacker mark an account object as imported so base64 / utf8 conversions used for signing payloads and signatures derives entropy from an account of their choosing?

## Target
- File/function: [src/utils/encodings.ts](src/utils/encodings.ts) - base64 / utf8 conversions used for signing payloads and signatures
- Entrypoint: generateAuthorizationSignature and every proxy signMessage payload
- Attacker controls: byte strings crossing the encode/decode boundary
- Exploit idea: Pass a hand-built account with imported true.
- Invariant to test: Account flags used for entropy selection must come from server-confirmed data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to base64 / utf8 conversions used for signing payloads and signatures and assert re-validation against the session user.
