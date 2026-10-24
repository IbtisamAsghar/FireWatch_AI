// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import { AlertRegistry } from "../src/AlertRegistry.sol";

interface AnchorVm {
    function envUint(string calldata name) external returns (uint256);
    function envAddress(string calldata name) external returns (address);
    function envBytes32(string calldata name) external returns (bytes32);
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

/// @notice Local acceptance only; durable production submissions belong to Phase 5.4.
contract AnchorLocalEvidence {
    AnchorVm private constant vm =
        AnchorVm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function run() external {
        require(block.chainid == 31337, "Local chain only");
        AlertRegistry registry = AlertRegistry(vm.envAddress("FIREWATCH_SMOKE_REGISTRY"));
        bytes32 key = vm.envBytes32("FIREWATCH_SMOKE_ALERT_KEY");
        bytes32 digest = vm.envBytes32("FIREWATCH_SMOKE_DIGEST");
        vm.startBroadcast(vm.envUint("FIREWATCH_DEPLOY_PRIVATE_KEY"));
        require(registry.anchor(key, digest), "First anchor must be new");
        require(!registry.anchor(key, digest), "Retry must be a no-op");
        vm.stopBroadcast();
    }
}
