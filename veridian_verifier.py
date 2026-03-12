"""
Core Verification Engine for VERIDIAN FLOW
Implements hybrid verification system with Merkle proofs and probabilistic sampling
"""
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any, Union
from uuid import uuid4

import numpy as np
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sklearn.metrics.pairwise import cosine_similarity

from firebase_setup import veridian_firebase


class VerificationMethod(Enum):
    """Supported verification methods"""
    MERKLE = "merkle"
    PROBABILISTIC = "probabilistic"
    OPTIMISTIC = "optimistic"
    CHALLENGE_RESPONSE = "challenge_response"


@dataclass
class TaskSpecification:
    """Universal Compute Interface task specification"""
    task_id: str = field(default_factory=lambda: str(uuid4()))
    docker_image: str
    resource_requirements: Dict[str, Union[int, float, str]]
    timeout_seconds: int
    verification_method: VerificationMethod
    input_data_hash: str
    expected_output_format: Dict[str, Any]
    bounty_amount_wei: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to Firestore-compatible dictionary"""
        return {
            'task_id': self.task_id,
            'docker_image': self.docker_image,
            'resource_requirements': self.resource_requirements,
            'timeout_seconds': self.timeout_seconds,
            'verification_method': self.verification_method.value,
            'input_data_hash': self.input_data_hash,
            'expected_output_format': self.expected_output_format,
            'bounty_amount_wei': self.bounty_amount_wei,
            'created_at': self.created_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskSpecification':
        """Create from Firestore dictionary"""
        return cls(
            task_id=data.get('task_id', str(uuid4())),
            docker_image=data['docker_image'],
            resource_requirements=data['resource_requirements'],
            timeout_seconds=data['timeout_seconds'],
            verification_method=VerificationMethod(data['verification_method']),
            input_data_hash=data['input_data_hash'],
            expected_output_format=data['expected_output_format'],
            bounty_amount_wei=data.get('bounty_amount_wei', 0),
            created_at=datetime.fromisoformat(data.get('created_at', datetime.now().isoformat()))
        )


@dataclass
class MerkleNode:
    """Merkle tree node for deterministic task verification"""
    hash: str
    left: Optional['MerkleNode'] = None
    right: Optional['MerkleNode'] = None
    data: Optional[Any] = None


class VeridianVerifier:
    """
    Core verification engine implementing:
    1. Merkle Proof Builder for deterministic tasks
    2. Probabilistic Sampling Validator for ML/statistical tasks
    3. Challenge-Response Protocol for game-theoretic security
    """
    
    def __init__(self, sampling_rate: float = 0.01):
        """
        Initialize verifier with configurable sampling rate
        
        Args:
            sampling_rate: Percentage of tasks to verify cryptographically (0.01 = 1%)
        """
        if not 0 < sampling_rate <= 1:
            raise ValueError("sampling_rate must be between 0 and 1")
        
        self.sampling_rate = sampling_rate
        self.logger = self._setup_logging()
        self.db = None
        self.stake_registry: Dict[str, Dict] = {}
        self._ec_private_key = ec.generate_private_key(ec.SECP256R1())
        self._ec_public_key = self._ec_private_key.public_key()
        
        # Initialize Firebase connection
        self._init_firebase()
    
    def _setup_logging(self) -> logging.Logger:
        """Configure structured logging"""
        logger = logging.getLogger('veridian_verifier')
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - VERIDIAN VERIFIER - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    def _init_firebase(self):
        """Initialize Firebase connection with error handling"""
        try:
            self.db = veridian_firebase.initialize()
            self.logger.info("Firebase connection established for verifier")
        except Exception as e:
            self.logger.error(f"Failed to initialize Firebase: {e}")
            # Continue in degraded mode (verifications stored locally)
            self.db = None
    
    def _store_verification_proof(self, proof: Dict[str, Any]) -> bool:
        """
        Store verification proof in Firestore with retry logic
        
        Returns:
            True if successful, False otherwise
        """
        if self.db is None:
            self.logger.warning("Firebase not available, proof stored locally only")
            return False
        
        try:
            proof_id = proof.get('proof_id', str(uuid4()))
            proof_ref = self.db.collection('verification_proofs').document(proof_id)
            
            # Add metadata
            proof['stored_at'] = datetime.now().isoformat()
            proof['firestore_path'] = f"verification_proofs/{proof_id}"
            
            proof_ref.set(proof)
            self.logger.debug(f"Verification proof stored: {proof_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to store verification proof: {e}")
            return False
    
    def build_merkle_tree(self, data_chunks: List[bytes]) -> Tuple[MerkleNode, Dict[str, MerkleNode]]:
        """
        Build Merkle tree from data chunks for deterministic computation verification
        
        Args:
            data_chunks: List of data chunks to include in tree
            
        Returns:
            Tuple of (root_node, node_map) where node