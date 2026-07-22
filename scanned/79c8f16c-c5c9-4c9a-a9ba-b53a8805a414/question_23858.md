Q23858: schema or resolution mix-up in compressed decode and codebook path when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}` with permissionless compressed-oracle signed slot updates while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that report version or timestamp-resolution dispatch decodes valid signed data under the wrong schema family along `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result`, corrupting decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected? This is the public read path that every later production quote depends on, so decoding mistakes are real loss surfaces if they ever slip through to live swaps. Submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{getOracleData,_decodeCodebookIndex} and smart-contracts-poc/contracts/oracles/utils/{Codebook256,U64x32,TimeMs}.sol
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result` in a live public flow and show that submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch. The exact value at risk is decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Invariant to test: Every verified report must be decoded by exactly the schema and time-resolution family it was signed for. The concrete assertion should cover decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Expected Immunefi impact: High bad-price execution if normalized oracle data is wrong despite successful verification.
- Fast validation: Read feeds containing sentinel and boundary codebook values and assert every decoded price/spread state matches the documented fail-closed behavior.
