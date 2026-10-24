// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import { AlertRegistry } from "../src/AlertRegistry.sol";

interface DeployVm {
    function envUint(string calldata name) external returns (uint256);
    function envAddress(string calldata name) external returns (address);
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

/// @dev Private key is supplied through a process-only environment variable, never argv.
contract DeployAlertRegistry {
    DeployVm private constant vm =
        DeployVm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function run() external returns (AlertRegistry registry) {
        require(block.chainid == vm.envUint("FIREWATCH_DEPLOY_CHAIN_ID"), "Unexpected chain ID");
        address writer = vm.envAddress("FIREWATCH_REGISTRY_WRITER");
        require(writer != address(0), "Writer required");
        vm.startBroadcast(vm.envUint("FIREWATCH_DEPLOY_PRIVATE_KEY"));
        registry = new AlertRegistry(writer);
        vm.stopBroadcast();
    }
}
