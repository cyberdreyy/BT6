# Q2698: bitcoin message decoded as UTF-8 in ConnectedStandardSolanaWallet.ts

## Question
EmbeddedBitcoinWalletProvider.sign decodes the message bytes with TextDecoder('utf8') before sending; can an attacker submit non-UTF-8 bytes so ConnectedStandardSolanaWallet.signMessage signs a replacement-character-mangled message?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Pass bytes containing 0x80-0xFF sequences and compare what is signed.
- Invariant to test: Message bytes must reach the signer unmodified.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass invalid UTF-8 through ConnectedStandardSolanaWallet.signMessage and assert byte-exact signing or rejection.
