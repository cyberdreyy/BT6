# Q2697: bitcoin message decoded as UTF-8 in offchain-message.ts

## Question
EmbeddedBitcoinWalletProvider.sign decodes the message bytes with TextDecoder('utf8') before sending; can an attacker submit non-UTF-8 bytes so deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) signs a replacement-character-mangled message?

## Target
- File/function: [src/solana/offchain-message.ts](src/solana/offchain-message.ts) - deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes), buildSolanaOffchainMessage (255 + 'solana offchain' preamble, 1232 max), parseSolanaOffchainMessage
- Entrypoint: off-chain message construction for Solana signing
- Attacker controls: origin/domain string, message contents and length, raw bytes handed to the parser
- Exploit idea: Pass bytes containing 0x80-0xFF sequences and compare what is signed.
- Invariant to test: Message bytes must reach the signer unmodified.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass invalid UTF-8 through deriveSolanaApplicationDomain (utf8 truncate/pad to 32 bytes) and assert byte-exact signing or rejection.
