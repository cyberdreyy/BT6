# Q2812: psbt forwarded without inspection in smart-wallets.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through smart-wallets entry (BICONOMY whose outputs differ from what the app displayed?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert smart-wallets entry (BICONOMY extracts and exposes psbt outputs for confirmation.
