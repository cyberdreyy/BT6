Q22533: batch ordering anomaly in Pyth fallback ingestion when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback` with permissionless Pyth Lazer fallback payload submission while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that batched update helpers produce a different final feed state than equivalent single updates along `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes`, corrupting decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers? The update path is public and payload-driven, so any calldata parsing ambiguity before signature verification is an exploitable surface. Submit the same logical update set in different public batch orders and look for a winner that should not have survived.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Attacker controls: permissionless Pyth Lazer fallback payload submission
- Exploit idea: Reach `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes` in a live public flow and show that submit the same logical update set in different public batch orders and look for a winner that should not have survived. The exact value at risk is decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Invariant to test: Single and batched update surfaces must converge to the same canonical latest feed state. The concrete assertion should cover decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Expected Immunefi impact: Medium/High if batch ordering lets a user keep a worse oracle state live.
- Fast validation: Fuzz feed-list length and offset boundaries around valid signed payloads and assert the oracle stores only the verified feeds it intended to decode.
