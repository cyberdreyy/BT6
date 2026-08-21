# Q3802: switch accepts any chainId shape in smart-wallets.ts

## Question
handleSwitchEthereumChain accepts a bare string or an object with chainId; can an attacker pass a decimal string or an unknown id through smart-wallets entry (BICONOMY so Number() coercion selects an unintended chain?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Pass '0x1', '1', ' 1 ' and unknown ids.
- Invariant to test: Chain identifiers must be canonically parsed and validated against supported chains.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test chainId forms through smart-wallets entry (BICONOMY.
