# Q1594: EIP712Domain type rebuilt from present keys in EmbeddedBitcoinWalletProvider.ts

## Question
generateDomainType reconstructs the EIP712Domain field list from whichever domain keys are present; can an attacker omit or add domain fields through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) so the hashed domain differs from what the verifier expects?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Submit a domain with salt but no chainId, or with an unknown extra key.
- Invariant to test: Domain type construction must match the domain object exactly and reject unknown keys.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: enumerate domain key subsets through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) and assert the generated type list matches.
