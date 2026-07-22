Q22683: delegation cleanup failure in Pyth fallback ingestion when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback` with permissionless compressed-oracle batch fallback pushes while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that removing or revoking a pusher leaves stale write authority that can still affect future updates along `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes`, corrupting decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers? The update path is public and payload-driven, so any calldata parsing ambiguity before signature verification is an exploitable surface. Publicly revoke or remove delegation, then continue pushing data and see whether writes still land in the old namespace.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Attacker controls: permissionless compressed-oracle batch fallback pushes
- Exploit idea: Reach `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes` in a live public flow and show that publicly revoke or remove delegation, then continue pushing data and see whether writes still land in the old namespace. The exact value at risk is decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Invariant to test: Delegation cleanup must fully remove the authority that later fallback or signed updates would otherwise reuse. The concrete assertion should cover decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Expected Immunefi impact: High if stale update authority can continue writing production feed data.
- Fast validation: Fuzz feed-list length and offset boundaries around valid signed payloads and assert the oracle stores only the verified feeds it intended to decode.
