# Q2806: psbt forwarded without inspection in isVersionedTransaction.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through isVersionedTransaction ('version' in tx) whose outputs differ from what the app displayed?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert isVersionedTransaction ('version' in tx) extracts and exposes psbt outputs for confirmation.
