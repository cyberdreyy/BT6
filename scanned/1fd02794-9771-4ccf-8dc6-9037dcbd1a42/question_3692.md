# Q3692: chain id switch emits an event apps trust in smart-wallets.ts

## Question
internalSwitchEthereumChain emits chainChanged after mutating internal state; can an attacker force a switch through smart-wallets entry (BICONOMY so the app's UI shows one chain while signing occurs on another?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Trigger a switch during a pending signature and compare the UI chain to the signed chainId.
- Invariant to test: The chain displayed and the chain signed must be identical for every signature.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: interleave a switch with a signature through smart-wallets entry (BICONOMY and assert consistency.
