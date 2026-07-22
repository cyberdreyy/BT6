Q23948: zero-state fail-open in compressed decode and codebook path when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}` with permissionless Chainlink Data Streams report submission while a registration or blacklist side effect happened shortly before the next live provider read, so that an uninitialized or zero-value feed state later looks like a valid quote instead of a halt condition along `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result`, corrupting decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected? This is the public read path that every later production quote depends on, so decoding mistakes are real loss surfaces if they ever slip through to live swaps. Read or route through a feed that has never been safely initialized and look for a valid-looking price path anyway.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{getOracleData,_decodeCodebookIndex} and smart-contracts-poc/contracts/oracles/utils/{Codebook256,U64x32,TimeMs}.sol
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{price,getOracleData}
- Attacker controls: permissionless Chainlink Data Streams report submission
- Exploit idea: Reach `public read -> feedId decode -> slot layout load -> compressed field decode -> price providers later consume the result` in a live public flow and show that read or route through a feed that has never been safely initialized and look for a valid-looking price path anyway. The exact value at risk is decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Invariant to test: Never-pushed or zero-state feeds must fail closed before any provider or pool can consume them. The concrete assertion should cover decoded mid price, spread0/spread1, timestamp, and whether sentinel values are treated as valid or rejected.
- Expected Immunefi impact: High if uninitialized feeds can still drive live swap pricing.
- Fast validation: Read feeds containing sentinel and boundary codebook values and assert every decoded price/spread state matches the documented fail-closed behavior.
