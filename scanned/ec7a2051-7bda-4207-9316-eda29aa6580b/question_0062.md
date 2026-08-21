# Q0062: chain silently switched by tx.chainId in smart-wallets.ts

## Question
EmbeddedWalletProvider.ensureChainId calls internalSwitchEthereumChain with the chainId found in the request params; can an unprivileged attacker submit a transaction through import {...} from '@privy-io/js-sdk-core/smart-wallets' whose chainId silently repoints the provider and the RPC client to another chain before signing?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Send eth_sendTransaction with a chainId the user never selected and observe chainChanged plus the new client.
- Invariant to test: The provider chain must only change through an explicit wallet_switchEthereumChain approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call smart-wallets entry (BICONOMY with a foreign chainId and assert no silent switch and no signature.
