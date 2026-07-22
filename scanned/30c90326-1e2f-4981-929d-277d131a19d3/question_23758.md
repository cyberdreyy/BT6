Q23758: batch ordering anomaly in compressed decode and codebook path when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}` with public oracle registration that later enables pool reads while multiple reports for the same feed arrive in different orders within one transaction or block, so that batched update helpers produce a different final feed state than equivalent single updates along `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result`, corrupting decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected? This is the public read path that every later production quote depends on, so decoding mistakes are real loss surfaces if they ever slip through to live swaps. Submit the same logical update set in different public batch orders and look for a winner that should not have survived.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{getOracleData,_decodeCodebookIndex} and smart-contracts-poc/contracts/oracles/utils/{Codebook256,U64x32,TimeMs}.sol
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result` in a live public flow and show that submit the same logical update set in different public batch orders and look for a winner that should not have survived. The exact value at risk is decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Invariant to test: Single and batched update surfaces must converge to the same canonical latest feed state. The concrete assertion should cover decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Expected Immunefi impact: Medium/High if batch ordering lets a user keep a worse oracle state live.
- Fast validation: Read feeds containing sentinel and boundary codebook values and assert every decoded price/spread state matches the documented fail-closed behavior.
