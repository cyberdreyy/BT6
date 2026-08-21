# Q2478: off-chain length header is two bytes in ConnectedStandardSolanaWallet.ts

## Question
buildSolanaOffchainMessage writes the message length as two little-endian bytes and caps the total at 1232; can an attacker craft a length that disagrees with the payload so ConnectedStandardSolanaWallet.signMessage or its parser reads a different message body?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Build and then parse a message whose declared length differs from the payload.
- Invariant to test: Declared length and payload must be verified equal on both build and parse.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: fuzz length/payload pairs through build and parse in ConnectedStandardSolanaWallet.signMessage.
