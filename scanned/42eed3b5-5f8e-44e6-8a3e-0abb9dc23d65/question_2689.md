# Q2689: personal_sign hex sniffing in resolve.ts

## Question
walletRpc treats any message starting with 0x as hex and slices two characters, otherwise utf-8; can an attacker submit a message beginning with 0x that is not valid hex so resolveCrypto: digest and randomUUID defaults from globalThis.crypto signs different bytes than the user saw?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Sign the string '0xhello world' and compare the bytes sent to the signer.
- Invariant to test: Message encoding selection must not change the bytes the user approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass '0xnothex' through resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert the signed bytes equal the displayed message.
