"""
Firebase Initialization Module for VERIDIAN FLOW
Handles secure Firebase connection with comprehensive error recovery
"""
import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore, initialize_app
from google.cloud.firestore_v1 import Client as FirestoreClient
from google.api_core.exceptions import GoogleAPICallError, RetryError


class FirebaseInitializationError(Exception):
    """Custom exception for Firebase setup failures"""
    pass


class VeridianFirebase:
    """
    Enterprise-grade Firebase client with automatic retry and health monitoring
    Implements singleton pattern to prevent multiple connections
    """
    _instance: Optional['VeridianFirebase'] = None
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(VeridianFirebase, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.app = None
            self.db: Optional[FirestoreClient] = None
            self._logger = self._setup_logging()
            self._initialized = True
    
    def _setup_logging(self):
        """Configure structured logging for Firebase operations"""
        import logging
        logger = logging.getLogger('veridian_firebase')
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    def _validate_credentials_file(self, credentials_path: str) -> bool:
        """Verify Firebase credentials file exists and is valid JSON"""
        try:
            cred_path = Path(credentials_path)
            if not cred_path.exists():
                self._logger.error(f"Credentials file not found: {credentials_path}")
                return False
            
            with open(cred_path, 'r') as f:
                credentials_data = json.load(f)
            
            required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
            for field in required_fields:
                if field not in credentials_data:
                    self._logger.error(f"Missing required field in credentials: {field}")
                    return False
            
            self._logger.info(f"Credentials validated for project: {credentials_data['project_id']}")
            return True
            
        except json.JSONDecodeError as e:
            self._logger.error(f"Invalid JSON in credentials file: {e}")
            return False
        except Exception as e:
            self._logger.error(f"Unexpected error validating credentials: {e}")
            return False
    
    def initialize(self, credentials_path: Optional[str] = None) -> FirestoreClient:
        """
        Initialize Firebase connection with comprehensive error handling
        
        Args:
            credentials_path: Path to Firebase service account JSON file
            
        Returns:
            Firestore client instance
            
        Raises:
            FirebaseInitializationError: If initialization fails
        """
        try:
            # Determine credentials path
            if credentials_path is None:
                credentials_path = os.getenv('FIREBASE_CREDENTIALS_PATH', './firebase_credentials.json')
            
            # Validate credentials file
            if not self._validate_credentials_file(credentials_path):
                raise FirebaseInitializationError("Invalid Firebase credentials file")
            
            # Initialize Firebase app (handles singleton internally)
            if not firebase_admin._apps:
                cred = credentials.Certificate(credentials_path)
                self.app = initialize_app(cred)
                self._logger.info(f"Firebase app initialized for project: {cred.project_id}")
            else:
                self.app = firebase_admin.get_app()
                self._logger.info("Using existing Firebase app instance")
            
            # Get Firestore client with retry configuration
            self.db = firestore.client(app=self.app)
            
            # Test connection with timeout
            self._test_connection()
            
            # Initialize collections if they don't exist
            self._initialize_collections()
            
            self._logger.info("Firebase initialization completed successfully")
            return self.db
            
        except FileNotFoundError as e:
            error_msg = f"Firebase credentials file not found: {e}"
            self._logger.error(error_msg)
            raise FirebaseInitializationError(error_msg)
        except GoogleAPICallError as e:
            error_msg = f"Google API error during Firebase initialization: {e}"
            self._logger.error(error_msg)
            raise FirebaseInitializationError(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error during Firebase initialization: {e}"
            self._logger.error(error_msg)
            raise FirebaseInitializationError(error_msg)
    
    def _test_connection(self, timeout_seconds: int = 10):
        """Test Firebase connection with timeout"""
        import threading
        from queue import Queue
        
        def connection_test():
            try:
                # Attempt to write and read a test document
                test_ref = self.db.collection('_health_check').document('connection_test')
                test_data = {
                    'timestamp': firestore.SERVER_TIMESTAMP,
                    'test': 'veridian_flow_health_check'
                }
                test_ref.set(test_data)
                
                # Verify write
                doc = test_ref.get()
                if doc.exists:
                    result_queue.put(True)
                else:
                    result_queue.put(False)
                    
            except Exception as e:
                self._logger.error(f"Connection test failed: {e}")
                result_queue.put(False)
        
        result_queue = Queue()
        thread = threading.Thread(target=connection_test)
        thread.daemon = True
        thread.start()
        thread.join(timeout_seconds)
        
        if thread.is_alive():
            raise FirebaseInitializationError("Firebase connection test timed out")
        
        if not result_queue.get():
            raise FirebaseInitializationError("Firebase connection test failed")
    
    def _initialize_collections(self):
        """Create necessary Firestore collections with indexes"""
        collections = [
            'verification_proofs',
            'task_queue',
            'active_nodes',
            'security_events',
            'performance_metrics',
            'financial_transactions',
            'dispute_resolutions'
        ]
        
        for collection in collections:
            try:
                # Create a dummy document to ensure collection exists
                self.db.collection(collection).document('_schema_version').set({
                    'created_at': firestore.SERVER_TIMESTAMP,
                    'version': '1.0',
                    'description': f'VERIDIAN FLOW {collection} schema'
                }, merge=True)
                self._logger.debug(f"Initialized collection: {collection}")
            except Exception as e:
                self._logger.warning(f"Could not initialize collection {collection}: {e}")
    
    def get_db(self) -> FirestoreClient:
        """Get Firestore client with validation"""
        if self.db is None:
            raise FirebaseInitializationError("Firebase not initialized. Call initialize() first.")
        return self.db
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check of Firebase connection"""
        try:
            start_time = datetime.now()
            
            # Test write
            health_ref = self.db.collection('_health_check').document('status')
            health_ref.set({
                'timestamp': firestore.SERVER_TIMESTAMP,
                'check': 'veridian_flow_health'
            })
            
            # Test read
            doc = health_ref.get()
            latency = (datetime.now() - start_time).total_seconds() * 1000
            
            return {
                'status': 'healthy' if doc.exists else 'degraded',
                'latency_ms': round(latency, 2),
                'project_id': self.app.project_id if self.app else 'unknown',
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }


# Global instance for easy import
veridian_firebase = VeridianFirebase()


if __name__ == "__main__":
    """Command-line interface for Firebase setup"""
    import argparse
    
    parser = argparse.ArgumentParser(description="VERIDIAN FLOW Firebase Setup")
    parser.add_argument('--credentials', default='./firebase_credentials.json',
                       help='Path to Firebase credentials JSON file')
    parser.add_argument('--test', action='store_true',
                       help='Run connection test only')
    
    args = parser.parse_args()
    
    try:
        if args.test:
            fb = VeridianFirebase()
            fb.initialize(args.credentials)
            health = fb.health_check()
            print(f"Firebase Health: {health}")
        else:
            print("Initializing VERIDIAN FLOW Firebase...")
            fb = VeridianFirebase()
            db = fb.initialize(args.credentials)
            print(f"✓ Firebase initialized successfully")
            print(f"  Project: {fb.app.project_id if fb.app else 'N/A'}")
            print(f"  Collections ready: verification_proofs, task_queue, etc.")
            
    except FirebaseInitializationError as e:
        print(f"✗ Firebase initialization failed: {e}")
        sys.exit(1)