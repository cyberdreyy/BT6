# Q0502: unsupported methods fall through to the public RPC in smart-wallets.ts

## Question
request() ends with handleJsonRpc, forwarding any unrecognised method to the chain RPC with the app id appended; can an attacker use smart-wallets entry (BICONOMY to proxy arbitrary JSON-RPC through the SDK's credentialed endpoint?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Call the provider with a non-standard method name and observe the forwarded request.
- Invariant to test: Only an allow-listed method set may be forwarded from src/smart-wallets.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call smart-wallets entry (BICONOMY with an arbitrary method and assert it is rejected.
