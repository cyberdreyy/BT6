# Q0054: chain silently switched by tx.chainId in EmbeddedBitcoinWalletProvider.ts

## Question
EmbeddedWalletProvider.ensureChainId calls internalSwitchEthereumChain with the chainId found in the request params; can an unprivileged attacker submit a transaction through bitcoinProvider.sign({message}) / .signTransaction({psbt}) whose chainId silently repoints the provider and the RPC client to another chain before signing?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Send eth_sendTransaction with a chainId the user never selected and observe chainChanged plus the new client.
- Invariant to test: The provider chain must only change through an explicit wallet_switchEthereumChain approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) with a foreign chainId and assert no silent switch and no signature.
