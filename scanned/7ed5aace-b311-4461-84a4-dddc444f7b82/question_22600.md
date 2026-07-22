Q22600: registration-side authorization bug in Pyth fallback ingestion when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback` with batched or repeated updates where newer and older reports race in the same block while multiple reports for the same feed arrive in different orders within one transaction or block, so that public registration enables more read authority or clears more blacklist state than intended along `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes`, corrupting decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers? The update path is public and payload-driven, so any calldata parsing ambiguity before signature verification is an exploitable surface. Pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/PythOracle.sol::fallback
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public fallback payload -> feed id list decode -> LazerConsumer verification -> oracleData writes` in a live public flow and show that pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized. The exact value at risk is decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Invariant to test: Registration and blacklist side effects must stay scoped to the exact pool/feed relation the caller paid for. The concrete assertion should cover decoded feed id list, payload offsets, verified feed contents, and the final oracleData entries later consumed by price providers.
- Expected Immunefi impact: High if unauthorized pools or providers can influence production price reads.
- Fast validation: Fuzz feed-list length and offset boundaries around valid signed payloads and assert the oracle stores only the verified feeds it intended to decode.
