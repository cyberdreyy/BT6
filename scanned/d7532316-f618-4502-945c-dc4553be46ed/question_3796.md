# Q3796: switch accepts any chainId shape in isVersionedTransaction.ts

## Question
handleSwitchEthereumChain accepts a bare string or an object with chainId; can an attacker pass a decimal string or an unknown id through isVersionedTransaction ('version' in tx) so Number() coercion selects an unintended chain?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Pass '0x1', '1', ' 1 ' and unknown ids.
- Invariant to test: Chain identifiers must be canonically parsed and validated against supported chains.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test chainId forms through isVersionedTransaction ('version' in tx).
