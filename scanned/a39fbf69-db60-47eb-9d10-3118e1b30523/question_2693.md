# Q2693: bitcoin message decoded as UTF-8 in EmbeddedSolanaWalletProvider.ts

## Question
EmbeddedBitcoinWalletProvider.sign decodes the message bytes with TextDecoder('utf8') before sending; can an attacker submit non-UTF-8 bytes so EmbeddedSolanaWalletProvider.request signs a replacement-character-mangled message?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Pass bytes containing 0x80-0xFF sequences and compare what is signed.
- Invariant to test: Message bytes must reach the signer unmodified.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass invalid UTF-8 through EmbeddedSolanaWalletProvider.request and assert byte-exact signing or rejection.
