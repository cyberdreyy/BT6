# Copyright (c) Mysten Labs, Inc.
# SPDX-License-Identifier: Apache-2.0

import json
import os

MAX_REPO = 25
SOURCE_REPO = 'MystenLabs/sui'
REPO_NAME = 'sui'
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    'bridge/move/tokens/btc/sources/btc.move',
    'bridge/move/tokens/eth/sources/eth.move',
    'bridge/move/tokens/usdc/sources/usdc.move',
    'bridge/move/tokens/usdt/sources/usdt.move',
    'crates/sui-bridge/src/abi.rs',
    'crates/sui-bridge/src/action_executor.rs',
    'crates/sui-bridge/src/client/bridge_authority_aggregator.rs',
    'crates/sui-bridge/src/client/bridge_client.rs',
    'crates/sui-bridge/src/client/mod.rs',
    'crates/sui-bridge/src/config.rs',
    'crates/sui-bridge/src/crypto.rs',
    'crates/sui-bridge/src/encoding.rs',
    'crates/sui-bridge/src/error.rs',
    'crates/sui-bridge/src/eth_client.rs',
    'crates/sui-bridge/src/eth_syncer.rs',
    'crates/sui-bridge/src/eth_transaction_builder.rs',
    'crates/sui-bridge/src/events.rs',
    'crates/sui-bridge/src/lib.rs',
    'crates/sui-bridge/src/metered_eth_provider.rs',
    'crates/sui-bridge/src/monitor.rs',
    'crates/sui-bridge/src/node.rs',
    'crates/sui-bridge/src/orchestrator.rs',
    'crates/sui-bridge/src/server/governance_verifier.rs',
    'crates/sui-bridge/src/server/handler.rs',
    'crates/sui-bridge/src/server/mod.rs',
    'crates/sui-bridge/src/storage.rs',
    'crates/sui-bridge/src/sui_bridge_watchdog/eth_bridge_status.rs',
    'crates/sui-bridge/src/sui_bridge_watchdog/eth_vault_balance.rs',
    'crates/sui-bridge/src/sui_bridge_watchdog/mod.rs',
    'crates/sui-bridge/src/sui_bridge_watchdog/sui_bridge_status.rs',
    'crates/sui-bridge/src/sui_bridge_watchdog/total_supplies.rs',
    'crates/sui-bridge/src/sui_client.rs',
    'crates/sui-bridge/src/sui_syncer.rs',
    'crates/sui-bridge/src/sui_transaction_builder.rs',
    'crates/sui-bridge/src/types.rs',
    'crates/sui-bridge/src/utils.rs',
    'crates/sui-core/src/accumulators/balances.rs',
    'crates/sui-core/src/accumulators/coin_reservations.rs',
    'crates/sui-core/src/accumulators/funds_read.rs',
    'crates/sui-core/src/accumulators/mod.rs',
    'crates/sui-core/src/accumulators/object_funds_checker/mod.rs',
    'crates/sui-core/src/accumulators/transaction_rewriting.rs',
    'crates/sui-core/src/admission_queue.rs',
    'crates/sui-core/src/authority.rs',
    'crates/sui-core/src/authority/authority_per_epoch_store.rs',
    'crates/sui-core/src/authority/authority_per_epoch_store_pruner.rs',
    'crates/sui-core/src/authority/authority_store.rs',
    'crates/sui-core/src/authority/authority_store_pruner.rs',
    'crates/sui-core/src/authority/authority_store_tables.rs',
    'crates/sui-core/src/authority/authority_store_types.rs',
    'crates/sui-core/src/authority/backpressure.rs',
    'crates/sui-core/src/authority/congestion_log.rs',
    'crates/sui-core/src/authority/consensus_quarantine.rs',
    'crates/sui-core/src/authority/consensus_tx_status_cache.rs',
    'crates/sui-core/src/authority/epoch_marker_key.rs',
    'crates/sui-core/src/authority/epoch_start_configuration.rs',
    'crates/sui-core/src/authority/execution_time_estimator.rs',
    'crates/sui-core/src/authority/finalized_transactions_cache.rs',
    'crates/sui-core/src/authority/shared_object_congestion_tracker.rs',
    'crates/sui-core/src/authority/shared_object_version_manager.rs',
    'crates/sui-core/src/authority/submitted_transaction_cache.rs',
    'crates/sui-core/src/authority/transaction_deferral.rs',
    'crates/sui-core/src/authority/transaction_reject_reason_cache.rs',
    'crates/sui-core/src/authority/weighted_moving_average.rs',
    'crates/sui-core/src/authority_aggregator.rs',
    'crates/sui-core/src/authority_client.rs',
    'crates/sui-core/src/authority_server.rs',
    'crates/sui-core/src/checkpoints/causal_order.rs',
    'crates/sui-core/src/checkpoints/checkpoint_executor/data_ingestion_handler.rs',
    'crates/sui-core/src/checkpoints/checkpoint_executor/mod.rs',
    'crates/sui-core/src/checkpoints/checkpoint_executor/utils.rs',
    'crates/sui-core/src/checkpoints/checkpoint_output.rs',
    'crates/sui-core/src/checkpoints/mod.rs',
    'crates/sui-core/src/congestion_tracker.rs',
    'crates/sui-core/src/consensus_adapter.rs',
    'crates/sui-core/src/consensus_commit_summary.rs',
    'crates/sui-core/src/consensus_handler.rs',
    'crates/sui-core/src/consensus_manager/mod.rs',
    'crates/sui-core/src/consensus_throughput_calculator.rs',
    'crates/sui-core/src/consensus_types/consensus_output_api.rs',
    'crates/sui-core/src/consensus_types/mod.rs',
    'crates/sui-core/src/consensus_validator.rs',
    'crates/sui-core/src/db_checkpoint_handler.rs',
    'crates/sui-core/src/epoch/committee_store.rs',
    'crates/sui-core/src/epoch/consensus_store_pruner.rs',
    'crates/sui-core/src/epoch/epoch_metrics.rs',
    'crates/sui-core/src/epoch/mod.rs',
    'crates/sui-core/src/epoch/randomness.rs',
    'crates/sui-core/src/epoch/reconfiguration.rs',
    'crates/sui-core/src/execution_cache.rs',
    'crates/sui-core/src/execution_cache/cache_types.rs',
    'crates/sui-core/src/execution_cache/object_locks.rs',
    'crates/sui-core/src/execution_cache/writeback_cache.rs',
    'crates/sui-core/src/execution_driver.rs',
    'crates/sui-core/src/execution_scheduler/execution_scheduler_impl.rs',
    'crates/sui-core/src/execution_scheduler/funds_withdraw_scheduler/address_funds/eager_scheduler/account_state.rs',
    'crates/sui-core/src/execution_scheduler/funds_withdraw_scheduler/address_funds/eager_scheduler/mod.rs',
    'crates/sui-core/src/execution_scheduler/funds_withdraw_scheduler/address_funds/eager_scheduler/pending_withdraw.rs',
    'crates/sui-core/src/execution_scheduler/funds_withdraw_scheduler/address_funds/mod.rs',
    'crates/sui-core/src/execution_scheduler/funds_withdraw_scheduler/address_funds/naive_scheduler.rs',
    'crates/sui-core/src/execution_scheduler/funds_withdraw_scheduler/address_funds/scheduler.rs',
    'crates/sui-core/src/execution_scheduler/funds_withdraw_scheduler/mod.rs',
    'crates/sui-core/src/execution_scheduler/mod.rs',
    'crates/sui-core/src/execution_scheduler/overload_tracker.rs',
    'crates/sui-core/src/execution_scheduler/settlement_scheduler.rs',
    'crates/sui-core/src/fallback_fetch.rs',
    'crates/sui-core/src/gasless_rate_limiter.rs',
    'crates/sui-core/src/global_state_hasher.rs',
    'crates/sui-core/src/jsonrpc_index.rs',
    'crates/sui-core/src/lib.rs',
    'crates/sui-core/src/module_cache_metrics.rs',
    'crates/sui-core/src/mysticeti_adapter.rs',
    'crates/sui-core/src/overload_monitor.rs',
    'crates/sui-core/src/post_consensus_tx_reorder.rs',
    'crates/sui-core/src/randomness_round_receiver.rs',
    'crates/sui-core/src/rpc_store_embed.rs',
    'crates/sui-core/src/rpc_store_ingestion_client.rs',
    'crates/sui-core/src/rpc_store_restore_source.rs',
    'crates/sui-core/src/rpc_store_streaming_client.rs',
    'crates/sui-core/src/runtime.rs',
    'crates/sui-core/src/safe_client.rs',
    'crates/sui-core/src/signature_verifier.rs',
    'crates/sui-core/src/stake_aggregator.rs',
    'crates/sui-core/src/status_aggregator.rs',
    'crates/sui-core/src/storage.rs',
    'crates/sui-core/src/streamer.rs',
    'crates/sui-core/src/subscription_handler.rs',
    'crates/sui-core/src/traffic_controller/mod.rs',
    'crates/sui-core/src/traffic_controller/nodefw_client.rs',
    'crates/sui-core/src/traffic_controller/policies.rs',
    'crates/sui-core/src/transaction_deny_config_manager.rs',
    'crates/sui-core/src/transaction_driver/effects_certifier.rs',
    'crates/sui-core/src/transaction_driver/error.rs',
    'crates/sui-core/src/transaction_driver/mod.rs',
    'crates/sui-core/src/transaction_driver/reconfig_observer.rs',
    'crates/sui-core/src/transaction_driver/request_retrier.rs',
    'crates/sui-core/src/transaction_driver/transaction_submitter.rs',
    'crates/sui-core/src/transaction_input_loader.rs',
    'crates/sui-core/src/transaction_orchestrator.rs',
    'crates/sui-core/src/transaction_outputs.rs',
    'crates/sui-core/src/transaction_signing_filter.rs',
    'crates/sui-core/src/validator_client_monitor/mod.rs',
    'crates/sui-core/src/validator_client_monitor/monitor.rs',
    'crates/sui-core/src/validator_client_monitor/stats.rs',
    'crates/sui-framework/packages/bridge/sources/bridge.move',
    'crates/sui-framework/packages/bridge/sources/chain_ids.move',
    'crates/sui-framework/packages/bridge/sources/committee.move',
    'crates/sui-framework/packages/bridge/sources/crypto.move',
    'crates/sui-framework/packages/bridge/sources/limiter.move',
    'crates/sui-framework/packages/bridge/sources/message.move',
    'crates/sui-framework/packages/bridge/sources/message_types.move',
    'crates/sui-framework/packages/bridge/sources/treasury.move',
    'crates/sui-framework/packages/move-stdlib/sources/address.move',
    'crates/sui-framework/packages/move-stdlib/sources/ascii.move',
    'crates/sui-framework/packages/move-stdlib/sources/bcs.move',
    'crates/sui-framework/packages/move-stdlib/sources/bit_vector.move',
    'crates/sui-framework/packages/move-stdlib/sources/bool.move',
    'crates/sui-framework/packages/move-stdlib/sources/debug.move',
    'crates/sui-framework/packages/move-stdlib/sources/fixed_point32.move',
    'crates/sui-framework/packages/move-stdlib/sources/hash.move',
    'crates/sui-framework/packages/move-stdlib/sources/internal.move',
    'crates/sui-framework/packages/move-stdlib/sources/macros.move',
    'crates/sui-framework/packages/move-stdlib/sources/option.move',
    'crates/sui-framework/packages/move-stdlib/sources/string.move',
    'crates/sui-framework/packages/move-stdlib/sources/type_name.move',
    'crates/sui-framework/packages/move-stdlib/sources/u128.move',
    'crates/sui-framework/packages/move-stdlib/sources/u16.move',
    'crates/sui-framework/packages/move-stdlib/sources/u256.move',
    'crates/sui-framework/packages/move-stdlib/sources/u32.move',
    'crates/sui-framework/packages/move-stdlib/sources/u64.move',
    'crates/sui-framework/packages/move-stdlib/sources/u8.move',
    'crates/sui-framework/packages/move-stdlib/sources/uq32_32.move',
    'crates/sui-framework/packages/move-stdlib/sources/uq64_64.move',
    'crates/sui-framework/packages/move-stdlib/sources/vector.move',
    'crates/sui-framework/packages/sui-framework/sources/accumulator.move',
    'crates/sui-framework/packages/sui-framework/sources/accumulator_metadata.move',
    'crates/sui-framework/packages/sui-framework/sources/accumulator_settlement.move',
    'crates/sui-framework/packages/sui-framework/sources/address.move',
    'crates/sui-framework/packages/sui-framework/sources/authenticator_state.move',
    'crates/sui-framework/packages/sui-framework/sources/bag.move',
    'crates/sui-framework/packages/sui-framework/sources/balance.move',
    'crates/sui-framework/packages/sui-framework/sources/bcs.move',
    'crates/sui-framework/packages/sui-framework/sources/borrow.move',
    'crates/sui-framework/packages/sui-framework/sources/clock.move',
    'crates/sui-framework/packages/sui-framework/sources/coin.move',
    'crates/sui-framework/packages/sui-framework/sources/config.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/bls12381.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/ecdsa_k1.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/ecdsa_r1.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/ecvrf.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/ed25519.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/groth16.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/group_ops.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/hash.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/hmac.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/nitro_attestation.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/poseidon.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/rangeproofs.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/ristretto255.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/vdf.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/zklogin_verified_id.move',
    'crates/sui-framework/packages/sui-framework/sources/crypto/zklogin_verified_issuer.move',
    'crates/sui-framework/packages/sui-framework/sources/deny_list.move',
    'crates/sui-framework/packages/sui-framework/sources/derived_object.move',
    'crates/sui-framework/packages/sui-framework/sources/display.move',
    'crates/sui-framework/packages/sui-framework/sources/dynamic_field.move',
    'crates/sui-framework/packages/sui-framework/sources/dynamic_object_field.move',
    'crates/sui-framework/packages/sui-framework/sources/event.move',
    'crates/sui-framework/packages/sui-framework/sources/funds_accumulator.move',
    'crates/sui-framework/packages/sui-framework/sources/hex.move',
    'crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk.move',
    'crates/sui-framework/packages/sui-framework/sources/kiosk/kiosk_extension.move',
    'crates/sui-framework/packages/sui-framework/sources/kiosk/transfer_policy.move',
    'crates/sui-framework/packages/sui-framework/sources/linked_table.move',
    'crates/sui-framework/packages/sui-framework/sources/math.move',
    'crates/sui-framework/packages/sui-framework/sources/object.move',
    'crates/sui-framework/packages/sui-framework/sources/object_bag.move',
    'crates/sui-framework/packages/sui-framework/sources/object_table.move',
    'crates/sui-framework/packages/sui-framework/sources/package.move',
    'crates/sui-framework/packages/sui-framework/sources/party.move',
    'crates/sui-framework/packages/sui-framework/sources/pay.move',
    'crates/sui-framework/packages/sui-framework/sources/priority_queue.move',
    'crates/sui-framework/packages/sui-framework/sources/protocol_config.move',
    'crates/sui-framework/packages/sui-framework/sources/prover.move',
    'crates/sui-framework/packages/sui-framework/sources/random.move',
    'crates/sui-framework/packages/sui-framework/sources/registries/coin_registry.move',
    'crates/sui-framework/packages/sui-framework/sources/registries/display_registry.move',
    'crates/sui-framework/packages/sui-framework/sources/scratch.move',
    'crates/sui-framework/packages/sui-framework/sources/sui.move',
    'crates/sui-framework/packages/sui-framework/sources/table.move',
    'crates/sui-framework/packages/sui-framework/sources/table_vec.move',
    'crates/sui-framework/packages/sui-framework/sources/token.move',
    'crates/sui-framework/packages/sui-framework/sources/transfer.move',
    'crates/sui-framework/packages/sui-framework/sources/tx_context.move',
    'crates/sui-framework/packages/sui-framework/sources/types.move',
    'crates/sui-framework/packages/sui-framework/sources/url.move',
    'crates/sui-framework/packages/sui-framework/sources/vec_map.move',
    'crates/sui-framework/packages/sui-framework/sources/vec_set.move',
    'crates/sui-framework/packages/sui-framework/sources/versioned.move',
    'crates/sui-node/src/handle.rs',
    'crates/sui-node/src/lib.rs',
    'crates/sui-types/src/accumulator_event.rs',
    'crates/sui-types/src/accumulator_metadata.rs',
    'crates/sui-types/src/accumulator_root.rs',
    'crates/sui-types/src/authenticator_state.rs',
    'crates/sui-types/src/balance.rs',
    'crates/sui-types/src/balance_change.rs',
    'crates/sui-types/src/base_types.rs',
    'crates/sui-types/src/bridge.rs',
    'crates/sui-types/src/clock.rs',
    'crates/sui-types/src/coin.rs',
    'crates/sui-types/src/coin_registry.rs',
    'crates/sui-types/src/coin_reservation.rs',
    'crates/sui-types/src/collection_types.rs',
    'crates/sui-types/src/committee.rs',
    'crates/sui-types/src/config.rs',
    'crates/sui-types/src/crypto.rs',
    'crates/sui-types/src/deny_list_v1.rs',
    'crates/sui-types/src/deny_list_v2.rs',
    'crates/sui-types/src/derived_object.rs',
    'crates/sui-types/src/digests.rs',
    'crates/sui-types/src/display.rs',
    'crates/sui-types/src/display_registry.rs',
    'crates/sui-types/src/dynamic_field.rs',
    'crates/sui-types/src/dynamic_field/visitor.rs',
    'crates/sui-types/src/effects/effects_v1.rs',
    'crates/sui-types/src/effects/effects_v2.rs',
    'crates/sui-types/src/effects/mod.rs',
    'crates/sui-types/src/effects/object_change.rs',
    'crates/sui-types/src/epoch_data.rs',
    'crates/sui-types/src/error.rs',
    'crates/sui-types/src/event.rs',
    'crates/sui-types/src/executable_transaction.rs',
    'crates/sui-types/src/execution.rs',
    'crates/sui-types/src/execution_params.rs',
    'crates/sui-types/src/execution_status.rs',
    'crates/sui-types/src/full_checkpoint_content.rs',
    'crates/sui-types/src/funds_accumulator.rs',
    'crates/sui-types/src/gas.rs',
    'crates/sui-types/src/gas_coin.rs',
    'crates/sui-types/src/gas_model/gas_predicates.rs',
    'crates/sui-types/src/gas_model/gas_v2.rs',
    'crates/sui-types/src/gas_model/mod.rs',
    'crates/sui-types/src/gas_model/tables.rs',
    'crates/sui-types/src/gas_model/units_types.rs',
    'crates/sui-types/src/global_state_hash.rs',
    'crates/sui-types/src/governance.rs',
    'crates/sui-types/src/id.rs',
    'crates/sui-types/src/in_memory_storage.rs',
    'crates/sui-types/src/inner_temporary_store.rs',
    'crates/sui-types/src/layout_resolver.rs',
    'crates/sui-types/src/lib.rs',
    'crates/sui-types/src/message_envelope.rs',
    'crates/sui-types/src/messages_checkpoint.rs',
    'crates/sui-types/src/messages_consensus.rs',
    'crates/sui-types/src/messages_grpc.rs',
    'crates/sui-types/src/messages_safe_client.rs',
    'crates/sui-types/src/move_package.rs',
    'crates/sui-types/src/multisig.rs',
    'crates/sui-types/src/multisig_legacy.rs',
    'crates/sui-types/src/nitro_attestation.rs',
    'crates/sui-types/src/node_role.rs',
    'crates/sui-types/src/object.rs',
    'crates/sui-types/src/object/balance_traversal.rs',
    'crates/sui-types/src/object/bounded_visitor.rs',
    'crates/sui-types/src/object/option_visitor.rs',
    'crates/sui-types/src/object/rpc_visitor/mod.rs',
    'crates/sui-types/src/object/rpc_visitor/proto.rs',
    'crates/sui-types/src/passkey_authenticator.rs',
    'crates/sui-types/src/programmable_transaction_builder.rs',
    'crates/sui-types/src/ptb_trace.rs',
    'crates/sui-types/src/randomness_state.rs',
    'crates/sui-types/src/rpc_proto_conversions.rs',
    'crates/sui-types/src/signature.rs',
    'crates/sui-types/src/signature_verification.rs',
    'crates/sui-types/src/storage/error.rs',
    'crates/sui-types/src/storage/mod.rs',
    'crates/sui-types/src/storage/object_store_trait.rs',
    'crates/sui-types/src/storage/read_store.rs',
    'crates/sui-types/src/storage/shared_in_memory_store.rs',
    'crates/sui-types/src/storage/write_store.rs',
    'crates/sui-types/src/sui_sdk_types_conversions.rs',
    'crates/sui-types/src/sui_serde.rs',
    'crates/sui-types/src/sui_system_state/epoch_start_sui_system_state.rs',
    'crates/sui-types/src/sui_system_state/mod.rs',
    'crates/sui-types/src/sui_system_state/sui_system_state_inner_v1.rs',
    'crates/sui-types/src/sui_system_state/sui_system_state_inner_v2.rs',
    'crates/sui-types/src/sui_system_state/sui_system_state_summary.rs',
    'crates/sui-types/src/supported_protocol_versions.rs',
    'crates/sui-types/src/traffic_control.rs',
    'crates/sui-types/src/transaction.rs',
    'crates/sui-types/src/transaction_deny_rules.rs',
    'crates/sui-types/src/transaction_driver_types.rs',
    'crates/sui-types/src/transaction_executor.rs',
    'crates/sui-types/src/transfer.rs',
    'crates/sui-types/src/type_input.rs',
    'crates/sui-types/src/versioned.rs',
    'crates/sui-types/src/zk_login_authenticator.rs',
    'crates/sui-types/src/zk_login_util.rs',
    'external-crates/move/crates/bytecode-interpreter-crypto/src/lib.rs',
    'external-crates/move/crates/move-abstract-interpreter/src/absint.rs',
    'external-crates/move/crates/move-abstract-interpreter/src/control_flow_graph.rs',
    'external-crates/move/crates/move-abstract-interpreter/src/lib.rs',
    'external-crates/move/crates/move-abstract-stack/src/lib.rs',
    'external-crates/move/crates/move-binary-format/src/binary_config.rs',
    'external-crates/move/crates/move-binary-format/src/check_bounds.rs',
    'external-crates/move/crates/move-binary-format/src/compatibility.rs',
    'external-crates/move/crates/move-binary-format/src/compatibility_mode.rs',
    'external-crates/move/crates/move-binary-format/src/constant.rs',
    'external-crates/move/crates/move-binary-format/src/deserializer.rs',
    'external-crates/move/crates/move-binary-format/src/errors.rs',
    'external-crates/move/crates/move-binary-format/src/file_format.rs',
    'external-crates/move/crates/move-binary-format/src/file_format_common.rs',
    'external-crates/move/crates/move-binary-format/src/inclusion_mode.rs',
    'external-crates/move/crates/move-binary-format/src/internals.rs',
    'external-crates/move/crates/move-binary-format/src/lib.rs',
    'external-crates/move/crates/move-binary-format/src/normalized.rs',
    'external-crates/move/crates/move-binary-format/src/serializer.rs',
    'external-crates/move/crates/move-borrow-graph/src/graph.rs',
    'external-crates/move/crates/move-borrow-graph/src/lib.rs',
    'external-crates/move/crates/move-borrow-graph/src/paths.rs',
    'external-crates/move/crates/move-borrow-graph/src/references.rs',
    'external-crates/move/crates/move-borrow-graph/src/shared.rs',
    'external-crates/move/crates/move-bytecode-verifier-meter/src/bound.rs',
    'external-crates/move/crates/move-bytecode-verifier-meter/src/dummy.rs',
    'external-crates/move/crates/move-bytecode-verifier-meter/src/lib.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/ability_cache.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/ability_field_requirements.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/absint.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/acquires_list_verifier.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/check_duplication.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/code_unit_verifier.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/constants.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/control_flow.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/control_flow_v5.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/cyclic_dependencies.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/data_defs.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/dependencies.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/friends.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/instantiation_loops.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/instruction_consistency.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/jump_table_usage_verifier.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/lib.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/limits.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/locals_safety/abstract_state.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/locals_safety/mod.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/loop_summary.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/reference_safety/abstract_state.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/reference_safety/mod.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/regex_reference_safety/abstract_state.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/regex_reference_safety/mod.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/regex_reference_safety/serializable_state.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/script_signature.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/signature.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/stack_usage_verifier.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/type_safety.rs',
    'external-crates/move/crates/move-bytecode-verifier/src/verifier.rs',
    'external-crates/move/crates/move-core-types/src/account_address.rs',
    'external-crates/move/crates/move-core-types/src/annotated_extractor.rs',
    'external-crates/move/crates/move-core-types/src/annotated_value.rs',
    'external-crates/move/crates/move-core-types/src/annotated_visitor.rs',
    'external-crates/move/crates/move-core-types/src/compressed/annotated/layout.rs',
    'external-crates/move/crates/move-core-types/src/compressed/annotated/mod.rs',
    'external-crates/move/crates/move-core-types/src/compressed/annotated/serde_impl.rs',
    'external-crates/move/crates/move-core-types/src/compressed/mod.rs',
    'external-crates/move/crates/move-core-types/src/compressed/runtime/layout.rs',
    'external-crates/move/crates/move-core-types/src/compressed/runtime/mod.rs',
    'external-crates/move/crates/move-core-types/src/compressed/runtime/serde_impl.rs',
    'external-crates/move/crates/move-core-types/src/gas_algebra.rs',
    'external-crates/move/crates/move-core-types/src/identifier.rs',
    'external-crates/move/crates/move-core-types/src/language_storage.rs',
    'external-crates/move/crates/move-core-types/src/lib.rs',
    'external-crates/move/crates/move-core-types/src/metadata.rs',
    'external-crates/move/crates/move-core-types/src/parsing/address.rs',
    'external-crates/move/crates/move-core-types/src/parsing/mod.rs',
    'external-crates/move/crates/move-core-types/src/parsing/parser.rs',
    'external-crates/move/crates/move-core-types/src/parsing/types.rs',
    'external-crates/move/crates/move-core-types/src/parsing/values.rs',
    'external-crates/move/crates/move-core-types/src/resolver.rs',
    'external-crates/move/crates/move-core-types/src/runtime_value.rs',
    'external-crates/move/crates/move-core-types/src/runtime_visitor.rs',
    'external-crates/move/crates/move-core-types/src/u256.rs',
    'external-crates/move/crates/move-core-types/src/vm_status.rs',
    'external-crates/move/crates/move-vm-config/src/lib.rs',
    'external-crates/move/crates/move-vm-config/src/runtime.rs',
    'external-crates/move/crates/move-vm-config/src/verifier.rs',
    'external-crates/move/crates/move-vm-runtime/src/cache/arena.rs',
    'external-crates/move/crates/move-vm-runtime/src/cache/identifier_interner.rs',
    'external-crates/move/crates/move-vm-runtime/src/cache/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/cache/move_cache.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/dispatch_tables.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/interpreter/eval.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/interpreter/helpers.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/interpreter/locals.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/interpreter/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/interpreter/state.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/tracing/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/tracing/tracer.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/values/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/values/values_impl.rs',
    'external-crates/move/crates/move-vm-runtime/src/execution/vm.rs',
    'external-crates/move/crates/move-vm-runtime/src/jit/execution/ast.rs',
    'external-crates/move/crates/move-vm-runtime/src/jit/execution/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/jit/execution/translate.rs',
    'external-crates/move/crates/move-vm-runtime/src/jit/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/jit/optimization/ast.rs',
    'external-crates/move/crates/move-vm-runtime/src/jit/optimization/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/jit/optimization/translate.rs',
    'external-crates/move/crates/move-vm-runtime/src/lib.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/extensions.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/functions.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/bcs.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/debug.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/hash.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/signer.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/string.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/type_name.rs',
    'external-crates/move/crates/move-vm-runtime/src/natives/move_stdlib/vector.rs',
    'external-crates/move/crates/move-vm-runtime/src/runtime/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/runtime/package_resolution.rs',
    'external-crates/move/crates/move-vm-runtime/src/runtime/telemetry.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/binary_cache.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/constants.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/gas.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/linkage_context.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/logging.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/safe_ops.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/types.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/views.rs',
    'external-crates/move/crates/move-vm-runtime/src/shared/vm_pointer.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/deserialization/ast.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/deserialization/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/deserialization/translate.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/verification/ast.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/verification/linkage.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/verification/mod.rs',
    'external-crates/move/crates/move-vm-runtime/src/validation/verification/translate.rs',
    'sui-execution/latest/sui-adapter/src/adapter.rs',
    'sui-execution/latest/sui-adapter/src/data_store/cached_package_store.rs',
    'sui-execution/latest/sui-adapter/src/data_store/mod.rs',
    'sui-execution/latest/sui-adapter/src/data_store/transaction_package_store.rs',
    'sui-execution/latest/sui-adapter/src/error.rs',
    'sui-execution/latest/sui-adapter/src/execution_engine.rs',
    'sui-execution/latest/sui-adapter/src/execution_mode.rs',
    'sui-execution/latest/sui-adapter/src/execution_value.rs',
    'sui-execution/latest/sui-adapter/src/gas_charger.rs',
    'sui-execution/latest/sui-adapter/src/gas_meter.rs',
    'sui-execution/latest/sui-adapter/src/lib.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/env.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/context.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/interpreter.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/trace_utils.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/execution/values.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/analysis.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/config.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/resolution.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/resolved_linkage.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/single_linkage.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/loading/ast.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/loading/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/loading/translate.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/metering/loading.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/metering/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/metering/pre_translation.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/metering/translation_meter.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/metering/typing.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/spanned.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/ast.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/invariant_checks/defining_ids_in_types.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/invariant_checks/memory_safety.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/invariant_checks/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/invariant_checks/type_check.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/translate.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/drop_safety.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/input_arguments.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/memory_safety.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/mod.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/move_functions.rs',
    'sui-execution/latest/sui-adapter/src/static_programmable_transactions/typing/verify/private_entry_arguments.rs',
    'sui-execution/latest/sui-adapter/src/temporary_store.rs',
    'sui-execution/latest/sui-adapter/src/temporary_store/invariants.rs',
    'sui-execution/latest/sui-adapter/src/type_layout_resolver.rs',
    'sui-execution/latest/sui-verifier/src/entry_points_verifier.rs',
    'sui-execution/latest/sui-verifier/src/global_storage_access_verifier.rs',
    'sui-execution/latest/sui-verifier/src/id_leak_verifier.rs',
    'sui-execution/latest/sui-verifier/src/lib.rs',
    'sui-execution/latest/sui-verifier/src/meter.rs',
    'sui-execution/latest/sui-verifier/src/one_time_witness_verifier.rs',
    'sui-execution/latest/sui-verifier/src/private_generics.rs',
    'sui-execution/latest/sui-verifier/src/private_generics_verifier_v2.rs',
    'sui-execution/latest/sui-verifier/src/struct_with_key_verifier.rs',
    'sui-execution/latest/sui-verifier/src/tx_context_restrictions_verifier.rs',
    'sui-execution/latest/sui-verifier/src/verifier.rs',
]

target_scopes = [
    'Critical. Unauthorized creation, duplication, transfer, release, withdrawal, destruction bypass, or custody escape of SUI, bridged assets, objects, or package-controlled value through verifier, runtime, bridge, ownership, or settlement failure',
    'Critical. Unauthorized package upgrade, dynamic loading, privilege escalation, or protected-state mutation by an unprivileged actor that breaks object ownership, transfer, type, or execution authority guarantees',
    'Critical. Irreversible fund lock, frozen withdrawal or redemption path, permanently unclaimable object or coin state, or unrecoverable bridged value in transaction, bridge, staking, accumulator, or settlement flows',
    'High. Replayable, non-unique, duplicated, stale-finalized, or cross-domain transaction, checkpoint, bridge message, nonce, proof, or settlement handling that causes double execution, double credit, or unbacked supply',
    'High. Acceptance of forged, stale, malformed, differently encoded, or insufficiently bound signatures, authenticators, proofs, bytecode, packages, bridge approvals, or verifier inputs that bypass execution gates',
    'High. Object ownership, balance, coin supply, bridge accounting, fee routing, refund, gas charging, or settlement-accounting divergence that sends value to the wrong party or breaks backing guarantees',
    'High. Transaction or contract execution flow manipulation that lets an unprivileged actor bypass deny-list, authenticator, dynamic-field, derived-object, capability, or transfer invariants with concrete asset or state impact',
    'Medium. Consensus, mempool, RPC, or peer-to-peer handling flaw reachable from unprivileged external input that deterministically causes validator shutdown, fullnode crash, or network instability on unmodified software',
    'Medium. Cryptographic domain-separation, hashing, signature-verification, or message-authenticity flaw that misbinds transaction, package, bridge, or protocol intent without requiring privileged keys',
    'Medium. Unintended permanent chain split, inconsistent finalization, or hard-fork-requiring state divergence caused by public input without assuming malicious validators, peers, or operators',
]

SUI_ALLOWED_IMPACT_SCOPE = '## Sui Allowed Impact Gate\nOnly accept active HackenProof Sui Protocol impacts as of July 23, 2026:\n- Critical: direct fund theft or state corruption from unauthorized object creation, copying, transfer, destruction, dynamic loading, or package upgrade; verifier/runtime bypass chained to theft; exceeding the 10B SUI cap; bridge message forgery or bridge governance or upgrade bypass that enables illegitimate mint or unlock; arbitrary non-Move RCE on unmodified validator software.\n- High or Medium: permanent fund lock, harmful smart-contract behavior, unintended permanent burning below the 10B cap, or the current-bounty network and node shutdown classes when reachable from public input.\n- Low: public-input fullnode crash or validator invariant-violation error on unmodified software.\nOut of scope: malicious peers, nodes, validators, bridge authorities, admins, or governance quorums; leaked keys or credentials; 51 percent, sybil, centralization, and liquidity claims; experimental account-alias policy; known 1-MIST rounding and 0x5 storage-rebate issues; tests, mocks, scripts, docs, readmes, toml, generated artifacts, gas-only DoS, and theory-only claims.'

SUI_AUDIT_PIVOTS = '## Smart Audit Pivots\n- Transaction path: signature or authenticator -> transaction input loading -> object, version, and ownership checks -> execution adapter -> temporary store -> effects and settlements.\n- Package path: BCS and binary deserialization -> bounds and file-format checks -> Move bytecode verifier -> Sui verifier -> runtime linkage, type, memory, and native execution rules.\n- State path: object id, derived object, dynamic field, package upgrade, coin, balance, transfer, deny-list, authenticator, staking, and accumulator invariants.\n- Bridge path: message encoding, chain-id domain separation, nonce or replay handling, committee or governance verification, and token mint, burn, lock, and unlock accounting.\n- Attacker model: unauthenticated caller, ordinary SUI holder, package publisher, bridge user, or contract caller only; never malicious peer, node, validator, bridge authority, admin, or governance quorum.'


def question_generator(target_file: str) -> str:
    """
    Generate focused Sui Protocol security questions for one scoped target.
    """

    prompt = f"""
    Generate Sui Protocol security questions for this exact scoped file:

    {target_file}

    Project lens:
    Focus on public transaction, package publish, package upgrade attempt, bridge-user, and RPC-reachable paths in Sui core, Sui types, Sui Framework Move modules, bridge code, Move binary format, bytecode verifier, Sui verifier, adapter, runtime, object model, authenticators, dynamic fields, derived objects, and token accounting.

    Bounty gate:
    {SUI_ALLOWED_IMPACT_SCOPE}

    {SUI_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file and `Scope:` as the only impact.
    * Assume repo context is available. Do not ask for code.
    * Attacker is unprivileged only: public caller, ordinary SUI holder, package publisher, bridge user, or contract caller.
    * Never rely on malicious peers, nodes, validators, bridge authorities, admins, governance quorums, leaked keys, or experimental account-alias behavior.
    * Exclude tests, mocks, scripts, docs, readmes, toml, generated artifacts, gas-only DoS, known 1-MIST or 0x5 false-positive classes, and theory-only claims.
    * Generate 18 to 26 high-signal questions. Avoid generic checklist items and repeated root causes.
    * Every question must name the exact corrupted value at risk and be testable with a unit, integration, property, or fork-style test.

    Each question must include target symbol, attacker-controlled input, required state, call path, invariant, corrupted value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled TX_OR_PACKAGE_OR_BRIDGE_INPUT under REQUIRED_ONCHAIN_STATE reach CALL_PATH and violate OWNERSHIP_OR_VERIFIER_OR_ACCOUNTING_INVARIANT, corrupting EXACT_OBJECT_BALANCE_PACKAGE_BRIDGE_OR_EFFECT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: build a reproducible test that drives the public path and asserts the invariant should fail closed.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused Sui exploit-question validation prompt.
    """
    return f"""# SUI PROTOCOL QUESTION REVIEW

## Exploit Question
{question}

## Scope Rules
- Audit only current-bounty Sui Protocol production code in this repository.
- Ignore tests, mocks, scripts, deployments, readmes, toml, generated artifacts, and docs-only issues.
- Do not ask for repo contents or claim files are missing.

## Objective
Decide whether the question leads to a real Sui Protocol vulnerability. The attacker must enter through a public transaction, package publish, package upgrade attempt, bridge-user input, or public RPC path available in scoped code.

Reject claims that need malicious peers, nodes, validators, bridge authorities, admins, governance quorums, leaked keys, experimental account-alias behavior, or already-known excluded bug classes. Prefer #NoVulnerability unless the path proves direct fund theft, state corruption, bridge illegitimate mint or unlock, permanent fund lock, or a current-bounty liveness or crash impact.

## Required Impacts
{SUI_ALLOWED_IMPACT_SCOPE}

{SUI_AUDIT_PIVOTS}

## Method
1. Trace the exact public or unprivileged entrypoint.
2. Map it to the exact scoped files and functions.
3. Follow input -> validation -> state transition -> corrupted value -> impact.
4. Identify the exact object, balance, package, bridge amount, effect, or runtime value that becomes wrong.
5. Reject if existing guards preserve the invariant or the impact misses the active bounty thresholds.

## Reject Immediately
- Any assumption requiring malicious peers, validators, bridge authorities, admins, or governance quorums.
- Package or bridge setup that is only unsafe because the trusted initializer chose unsafe parameters.
- Experimental account-alias behavior or known excluded 1-MIST or 0x5 issue families.
- Gas-only DoS, crashes below bounty thresholds, logs, style, dependency-only behavior, and docs-only claims.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for Sui issues.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Search this Sui repository for a native analog in public transaction, package, verifier, runtime, framework, or bridge code that matches the same root cause under the unprivileged-user model.

## Required Impacts
{SUI_ALLOWED_IMPACT_SCOPE}

{SUI_AUDIT_PIVOTS}

Report only if this repository has its own reachable root cause, public trigger, broken invariant, exact corrupted value, and matching target scope or allowed impact. Reject privileged operations, malicious peer or node assumptions, malicious governance or bridge quorum assumptions, excluded known issues, tooling-only behavior, and anything outside production scope.

## Work Plan
1. Classify the external bug into one Sui invariant.
2. Map it to exact scoped files and functions.
3. Trace attacker input through production validation and state transitions.
4. Identify the wrong object, balance, bridge amount, package state, effect, or runtime value.
5. Reject if existing guards preserve the invariant or the loss is not bounty-relevant.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict Sui validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim against current-bounty Sui Protocol production code in this repository.
- Do not invent a stronger claim, change target scope, or upgrade severity without evidence.
- A valid issue must be triggered by an unprivileged public caller, ordinary SUI holder, package publisher, bridge user, or contract caller.
- Reject malicious peer, node, validator, bridge authority, admin, governance, leaked-key, and experimental account-alias assumptions.
- Reject known excluded 1-MIST and 0x5 issue families, tests, mocks, scripts, docs, readmes, toml, generated artifacts, gas-only DoS, style, dependency-only bugs, and theory-only claims.
- The final impact must match one `target_scopes` item or the allowed impacts below, identify the exact corrupted value, and satisfy the active HackenProof Sui Protocol rules as of July 23, 2026.

## Required Impacts
{SUI_ALLOWED_IMPACT_SCOPE}

{SUI_AUDIT_PIVOTS}

## Required Checks
1. Exact file and function references in scoped code.
2. Clear broken Sui invariant tied to funds, state corruption, bridge accounting, package authority, runtime safety, or current-bounty liveness or crash impact.
3. Reachable exploit path: preconditions -> attacker input -> production call path -> wrong value.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: object owner, object version, balance, coin supply, bridge amount, bridge message, package id, type, authenticator result, effect, or runtime state.
6. Reproducible proof path: unit, integration, property, or fork-style test.

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
