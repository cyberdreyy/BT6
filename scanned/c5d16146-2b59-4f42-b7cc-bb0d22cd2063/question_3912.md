# Q3912: tempo path selected by a predicate on the request in smart-wallets.ts

## Question
The provider routes to the Tempo serializer when isTempoTransactionRequest matches; can an attacker shape a request so smart-wallets entry (BICONOMY takes the Tempo path on a non-Tempo chain, or the standard path for a Tempo transaction?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Submit hybrid field sets and compare the serialised output to the target chain.
- Invariant to test: Serializer selection must agree with the target chain and be rejected otherwise.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit hybrid requests to smart-wallets entry (BICONOMY and assert consistent routing.
