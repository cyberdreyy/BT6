# Q3794: switch accepts any chainId shape in EmbeddedBitcoinWalletProvider.ts

## Question
handleSwitchEthereumChain accepts a bare string or an object with chainId; can an attacker pass a decimal string or an unknown id through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes) so Number() coercion selects an unintended chain?

## Target
- File/function: [src/embedded/EmbeddedBitcoinWalletProvider.ts](src/embedded/EmbeddedBitcoinWalletProvider.ts) - EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes), signTransaction (psbt), request
- Entrypoint: bitcoinProvider.sign({message}) / .signTransaction({psbt})
- Attacker controls: raw message bytes, psbt hex/base64 payload
- Exploit idea: Pass '0x1', '1', ' 1 ' and unknown ids.
- Invariant to test: Chain identifiers must be canonically parsed and validated against supported chains.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test chainId forms through EmbeddedBitcoinWalletProvider.sign (TextDecoder utf8 decode of message bytes).
