# Q3798: switch accepts any chainId shape in ConnectedStandardSolanaWallet.ts

## Question
handleSwitchEthereumChain accepts a bare string or an object with chainId; can an attacker pass a decimal string or an unknown id through ConnectedStandardSolanaWallet.signMessage so Number() coercion selects an unintended chain?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Pass '0x1', '1', ' 1 ' and unknown ids.
- Invariant to test: Chain identifiers must be canonically parsed and validated against supported chains.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test chainId forms through ConnectedStandardSolanaWallet.signMessage.
