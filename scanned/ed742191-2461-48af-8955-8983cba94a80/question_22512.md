Q22512: packed-slot decode confusion in Pyth fallback ingestion when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback` with batched or repeated updates where newer and older reports race in the same block while a registration or blacklist side effect happened shortly before the next live provider read, so that slot packing, sentinel markers, or codebook boundaries decode into a valid-looking price or spread that should have failed closed along `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes`, corrupting decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers? The update path is public and payload-driven, so any calldata parsing ambiguity before signature verification is an exploitable surface. Push or read a boundary compressed value whose spread or timestamp representation is easy to misinterpret.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes` in a live public flow and show that push or read a boundary compressed value whose spread or timestamp representation is easy to misinterpret. The exact value at risk is decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Invariant to test: Packed compressed data must decode unambiguously and reject every sentinel or malformed boundary state before price consumers trust it. The concrete assertion should cover decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Expected Immunefi impact: Critical if a malformed compressed value can drive live pool pricing.
- Fast validation: Fuzz feed-list length and offset boundaries around valid signed payloads and assert the oracle stores only the verified feeds it intended to decode.
