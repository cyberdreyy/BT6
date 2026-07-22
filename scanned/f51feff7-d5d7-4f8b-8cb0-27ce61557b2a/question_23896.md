Q23896: delegation cleanup failure in compressed decode and codebook path when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}` with batched or repeated updates where newer and older reports race in the same block while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that removing or revoking a pusher leaves stale write authority that can still affect future updates along `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result`, corrupting decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected? This is the public read path that every later production quote depends on, so decoding mistakes are real loss surfaces if they ever slip through to live swaps. Publicly revoke or remove delegation, then continue pushing data and see whether writes still land in the old namespace.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{getOracleData,_decodeCodebookIndex} and smart-contracts-poc/contracts/oracles/utils/{Codebook256,U64x32,TimeMs}.sol
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result` in a live public flow and show that publicly revoke or remove delegation, then continue pushing data and see whether writes still land in the old namespace. The exact value at risk is decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Invariant to test: Delegation cleanup must fully remove the authority that later fallback or signed updates would otherwise reuse. The concrete assertion should cover decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Expected Immunefi impact: High if stale update authority can continue writing production feed data.
- Fast validation: Read feeds containing sentinel and boundary codebook values and assert every decoded price/spread state matches the documented fail-closed behavior.
