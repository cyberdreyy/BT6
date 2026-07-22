Q23609: signature replay or domain-separation bug in compressed decode and codebook path when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}` with permissionless compressed-oracle pusher delegation and revocation calls while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that a valid signature for one context is also accepted in another context that changes who controls a namespace or feed update along `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result`, corrupting decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected? This is the public read path that every later production quote depends on, so decoding mistakes are real loss surfaces if they ever slip through to live swaps. Reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{getOracleData,_decodeCodebookIndex} and smart-contracts-poc/contracts/oracles/utils/{Codebook256,U64x32,TimeMs}.sol
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}
- Attacker controls: permissionless compressed-oracle pusher delegation and revocation calls
- Exploit idea: Reach `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result` in a live public flow and show that reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope. The exact value at risk is decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Invariant to test: Every signed oracle action must be bound to one exact creator, chain, contract, and action payload. The concrete assertion should cover decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Expected Immunefi impact: Critical if a public attacker can hijack update authority over a production feed namespace.
- Fast validation: Read feeds containing sentinel and boundary codebook values and assert every decoded price/spread state matches the documented fail-closed behavior.
