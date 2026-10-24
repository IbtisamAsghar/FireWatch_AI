// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import { AlertRegistry } from "../src/AlertRegistry.sol";

interface TestVm {
    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    function prank(address sender) external;
    function expectRevert(bytes4 selector) external;
    function expectRevert(bytes calldata data) external;
    function expectEmit(bool, bool, bool, bool, address) external;
    function recordLogs() external;
    function getRecordedLogs() external returns (Log[] memory);
    function assume(bool condition) external;
}

contract AlertRegistryTest {
    TestVm private constant vm = TestVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    AlertRegistry private registry;
    address private constant WRITER = address(0xA11CE);
    address private constant OTHER = address(0xB0B);
    bytes32 private constant KEY = bytes32(uint256(1));
    bytes32 private constant DIGEST =
        0x73d118d5f411518276739564a036f7bee21c75f84147aeff832131c1e2017f2b;

    event EvidenceAnchored(
        bytes32 indexed alertKey, bytes32 indexed evidenceDigest, address indexed writer
    );

    function setUp() public {
        registry = new AlertRegistry(WRITER);
    }

    function testWriterIsPinned() public view {
        require(registry.writer() == WRITER);
    }

    function testZeroWriterRejected() public {
        vm.expectRevert(AlertRegistry.InvalidWriter.selector);
        new AlertRegistry(address(0));
    }

    function testMissingKeyReadsZero() public view {
        require(registry.evidenceDigest(KEY) == bytes32(0));
    }

    function testAuthorizedWriteStoresExactSha256AndEvent() public {
        require(DIGEST == sha256("synthetic FireWatch evidence"));
        vm.expectEmit(true, true, true, true, address(registry));
        emit EvidenceAnchored(KEY, DIGEST, WRITER);
        vm.prank(WRITER);
        require(registry.anchor(KEY, DIGEST));
        require(registry.evidenceDigest(KEY) == DIGEST);
        require(DIGEST != keccak256("synthetic FireWatch evidence"));
    }

    function testUnauthorizedWriteRejected() public {
        vm.expectRevert(AlertRegistry.UnauthorizedWriter.selector);
        vm.prank(OTHER);
        registry.anchor(KEY, DIGEST);
        require(registry.evidenceDigest(KEY) == bytes32(0));
    }

    function testDeployerIsNotAutomaticallyWriter() public {
        vm.expectRevert(AlertRegistry.UnauthorizedWriter.selector);
        registry.anchor(KEY, DIGEST);
    }

    function testIdenticalRetryIsNoOpAndDoesNotEmit() public {
        vm.prank(WRITER);
        registry.anchor(KEY, DIGEST);
        vm.recordLogs();
        vm.prank(WRITER);
        require(!registry.anchor(KEY, DIGEST));
        require(vm.getRecordedLogs().length == 0);
        require(registry.evidenceDigest(KEY) == DIGEST);
    }

    function testUnauthorizedIdenticalRetryRejected() public {
        vm.prank(WRITER);
        registry.anchor(KEY, DIGEST);
        vm.expectRevert(AlertRegistry.UnauthorizedWriter.selector);
        vm.prank(OTHER);
        registry.anchor(KEY, DIGEST);
    }

    function testConflictingOverwriteRejected() public {
        vm.prank(WRITER);
        registry.anchor(KEY, DIGEST);
        bytes32 changed = sha256("changed");
        vm.expectRevert(
            abi.encodeWithSelector(AlertRegistry.ConflictingDigest.selector, KEY, DIGEST, changed)
        );
        vm.prank(WRITER);
        registry.anchor(KEY, changed);
        require(registry.evidenceDigest(KEY) == DIGEST);
    }

    function testEmptyKeyRejected() public {
        vm.expectRevert(AlertRegistry.EmptyAlertKey.selector);
        vm.prank(WRITER);
        registry.anchor(bytes32(0), DIGEST);
    }

    function testEmptyDigestRejected() public {
        vm.expectRevert(AlertRegistry.EmptyEvidenceDigest.selector);
        vm.prank(WRITER);
        registry.anchor(KEY, bytes32(0));
    }

    function testSameDigestAcrossDifferentKeysAllowed() public {
        vm.prank(WRITER);
        registry.anchor(KEY, DIGEST);
        vm.prank(WRITER);
        registry.anchor(bytes32(uint256(2)), DIGEST);
        require(registry.evidenceDigest(KEY) == DIGEST);
        require(registry.evidenceDigest(bytes32(uint256(2))) == DIGEST);
    }

    function testUnknownSelectorCannotChangeWriter() public {
        (bool success,) =
            address(registry).call(abi.encodeWithSignature("setWriter(address)", OTHER));
        require(!success && registry.writer() == WRITER);
    }

    function testFuzzExactBytesAndAuthorizedRetry(bytes32 key, bytes32 digest) public {
        vm.assume(key != bytes32(0) && digest != bytes32(0));
        vm.prank(WRITER);
        require(registry.anchor(key, digest));
        vm.prank(WRITER);
        require(!registry.anchor(key, digest));
        require(registry.evidenceDigest(key) == digest);
    }

    function testFuzzConflictsNeverOverwrite(bytes32 key, bytes32 first, bytes32 second) public {
        vm.assume(
            key != bytes32(0) && first != bytes32(0) && second != bytes32(0) && first != second
        );
        vm.prank(WRITER);
        registry.anchor(key, first);
        vm.expectRevert(
            abi.encodeWithSelector(AlertRegistry.ConflictingDigest.selector, key, first, second)
        );
        vm.prank(WRITER);
        registry.anchor(key, second);
        require(registry.evidenceDigest(key) == first);
    }

    function testFuzzUnauthorizedNeverWrites(address caller, bytes32 key, bytes32 digest) public {
        vm.assume(caller != WRITER);
        vm.expectRevert(AlertRegistry.UnauthorizedWriter.selector);
        vm.prank(caller);
        registry.anchor(key, digest);
        require(registry.evidenceDigest(key) == bytes32(0));
    }
}
