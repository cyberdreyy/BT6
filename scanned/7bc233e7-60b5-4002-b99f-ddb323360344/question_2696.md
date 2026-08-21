# Q2696: bitcoin message decoded as UTF-8 in isVersionedTransaction.ts

## Question
EmbeddedBitcoinWalletProvider.sign decodes the message bytes with TextDecoder('utf8') before sending; can an attacker submit non-UTF-8 bytes so isVersionedTransaction ('version' in tx) signs a replacement-character-mangled message?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Pass bytes containing 0x80-0xFF sequences and compare what is signed.
- Invariant to test: Message bytes must reach the signer unmodified.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass invalid UTF-8 through isVersionedTransaction ('version' in tx) and assert byte-exact signing or rejection.
