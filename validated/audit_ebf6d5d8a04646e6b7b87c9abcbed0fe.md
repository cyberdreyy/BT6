[1](#0-0) [2](#0-1)

### Citations

**File:** libsigner/src/v0/messages.rs (L2978-3018)
```rust
                SignerMessage::BlockResponse(BlockResponse::Rejected(BlockRejection {
                    reason_code: RejectCode::ValidationFailed(ValidateRejectCode::NoSuchTenure),
                    reason: "Block is not a tenure-start block, and has an unrecognized tenure consensus hash".to_string(),
                    signer_signature_hash: Sha512Trunc256Sum::from_hex("91f95f84b7045f7dce7757052caa986ef042cb58f7df5031a3b5b5d0e3dda63e").unwrap(),
                    chain_id: CHAIN_ID_TESTNET,
                    signature: MessageSignature::from_hex("006fb349212e1a1af1a3c712878d5159b5ec14636adb6f70be00a6da4ad4f88a9934d8a9abb229620dd8e0f225d63401e36c64817fb29e6c05591dcbe95c512df3").unwrap(),
                    metadata: SignerMessageMetadata {
                        server_version: "Hello world".to_string(),
                    },
                    response_data: BlockResponseData {
                        version: 3,
                        tenure_extend_timestamp: 11,
                        reject_reason: RejectReason::InvalidParentBlock,
                        tenure_extend_read_count_timestamp: u64::MAX,
                        failed_txid: None,
                        unknown_bytes: vec![],
                    },
                })),
                "010100000050426c6f636b206973206e6f7420612074656e7572652d737461727420626c6f636b2c20616e642068617320616e20756e7265636f676e697a65642074656e75726520636f6e73656e7375732068617368000691f95f84b7045f7dce7757052caa986ef042cb58f7df5031a3b5b5d0e3dda63e80000000006fb349212e1a1af1a3c712878d5159b5ec14636adb6f70be00a6da4ad4f88a9934d8a9abb229620dd8e0f225d63401e36c64817fb29e6c05591dcbe95c512df30000000b48656c6c6f20776f726c640300000009000000000000000b0b",
            ),
            (
                SignerMessage::BlockResponse(BlockResponse::Accepted(BlockAccepted {
                    signer_signature_hash: Sha512Trunc256Sum::from_hex(
                        "11717149677c2ac97d15ae5954f7a716f10100b9cb81a2bf27551b2f2e54ef19"
                    )
                        .unwrap(),
                    metadata: SignerMessageMetadata {
                        server_version: "Hello world".to_string(),
                    },
                    signature: MessageSignature::from_hex("001c694f8134c5c90f2f2bcd330e9f423204884f001b5df0050f36a2c4ff79dd93522bb2ae395ea87de4964886447507c18374b7a46ee2e371e9bf332f0706a3e8").unwrap(),
                    response_data: BlockResponseData {
                        version: 3,
                        tenure_extend_timestamp: 21,
                        reject_reason: RejectReason::NotRejected,
                        tenure_extend_read_count_timestamp: u64::MAX,
                        failed_txid: None,
                        unknown_bytes: vec![],
                    },
                })),
                "010011717149677c2ac97d15ae5954f7a716f10100b9cb81a2bf27551b2f2e54ef19001c694f8134c5c90f2f2bcd330e9f423204884f001b5df0050f36a2c4ff79dd93522bb2ae395ea87de4964886447507c18374b7a46ee2e371e9bf332f0706a3e80000000b48656c6c6f20776f726c6403000000090000000000000015ff",
            )
```

**File:** libsigner/src/v0/messages.rs (L3021-3040)
```rust
        for (expected_out, input_hex) in test_vectors.into_iter() {
            let input_bytes = hex_bytes(input_hex).unwrap();
            let actual_out =
                SignerMessage::consensus_deserialize(&mut input_bytes.as_slice()).unwrap();
            assert_eq!(actual_out, expected_out);
            let SignerMessage::BlockResponse(expected_out) = expected_out else {
                panic!("Expected block response");
            };
            let SignerMessage::BlockResponse(actual_out) = actual_out else {
                panic!("Expected block response");
            };
            let expected_data = expected_out.get_response_data();
            let resp_data = actual_out.get_response_data();
            assert_eq!(
                resp_data.tenure_extend_read_count_timestamp,
                expected_data.tenure_extend_read_count_timestamp
            );
            assert_eq!(resp_data.unknown_bytes, expected_data.unknown_bytes);
            assert_eq!(resp_data.version, expected_data.version);
            assert_eq!(resp_data, expected_data);
```
