# Q1158: fee payer signature parity inference in ConnectedStandardSolanaWallet.ts

## Question
toFeePayerSignature derives yParity from v-27 when yParity is absent; can an attacker supply a v value that yields a wrong parity accepted by ConnectedStandardSolanaWallet.signMessage?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Send v values such as 0, 1, 35 and 36 and inspect the derived parity.
- Invariant to test: Signature parity must be derived unambiguously or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test v/yParity inputs through ConnectedStandardSolanaWallet.signMessage.
