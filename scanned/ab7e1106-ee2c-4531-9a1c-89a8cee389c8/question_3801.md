# Q3801: switch accepts any chainId shape in unified-wallet.ts

## Question
handleSwitchEthereumChain accepts a bare string or an object with chainId; can an attacker pass a decimal string or an unknown id through isUnifiedWallet (account.id && recovery_method === 'privy-v2') so Number() coercion selects an unintended chain?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Pass '0x1', '1', ' 1 ' and unknown ids.
- Invariant to test: Chain identifiers must be canonically parsed and validated against supported chains.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test chainId forms through isUnifiedWallet (account.id && recovery_method === 'privy-v2').
