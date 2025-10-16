#!/usr/bin/env python3
"""
Sway Manager - Handles Sway configuration and management.

This module provides functionality to generate, manage, and reload Sway configurations.
It handles the Sway IPC socket communication and provides methods to control the Sway
window manager programmatically.
"""
import os
import sys
import time
import signal
import logging
import subprocess
import argparse
from typing import Optional
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('sway_manager')

class SwayManager:
    
    def __init__(self, config_path: str):
        self.config_path = os.path.abspath(config_path)
        self.running = False
        self.sway_process = None
        
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        
        # Set up paths
        self.config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            'config'
        )
        self.template_path = os.path.join(self.config_dir, 'sway_config_template.in')
        self.sway_config_path = os.path.join(self.config_dir, 'sway_config')
        
        try:
            # Ensure config directory exists
            os.makedirs(self.config_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create config directory: {e}")
            raise
    
    def _generate_sway_config(self) -> bool:
        logger.info(f"Generating Sway configuration from template: {self.template_path}")
        
        if not os.path.exists(self.template_path):
            error_msg = f"Sway config template not found at {self.template_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        
        # Load streams configuration
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            streams = config.get('streams', [])
        except Exception as e:
            logger.error(f"Failed to load streams configuration: {e}")
            return False
            
        try:
            # Read template with explicit encoding
            with open(self.template_path, 'r', encoding='utf-8') as f:
                template = f.read()
            
            # Generate window positioning commands for each stream
            window_rules = []
            for stream in streams:
                geo = stream.get('geometry', {})
                x = geo.get('x', 0)
                y = geo.get('y', 0)
                width = geo.get('width', 640)
                height = geo.get('height', 360)
                
                # Create window rule for this stream
                rule = f"for_window [title='{stream['id']}'] move position {x} {y}"
                window_rules.append(rule)
            
            # Combine template with generated window rules
            config_content = template
            if window_rules:
                config_content = template + "\n\n" + "\n\n".join(window_rules)
            
            # Ensure the output directory exists
            os.makedirs(os.path.dirname(self.sway_config_path), exist_ok=True)
            
            # Write config file with explicit encoding
            with open(self.sway_config_path, 'w', encoding='utf-8') as f:
                f.write(config_content)
            
            # Set appropriate permissions
            os.chmod(self.sway_config_path, 0o644)
            
            logger.info(f"Successfully generated Sway config at {self.sway_config_path}")
            return True
            
        except PermissionError as e:
            error_msg = f"Permission denied when accessing {self.template_path} or {self.sway_config_path}"
            logger.error(error_msg)
            raise IOError(error_msg) from e
        except Exception as e:
            error_msg = f"Error generating Sway config: {e}"
            logger.error(error_msg, exc_info=True)
            raise IOError(error_msg) from e
    
    def _start_sway(self) -> bool:
        """Start the Sway window manager with the generated configuration.
        
        Returns:
            bool: True if Sway started successfully, False otherwise
        """
        if self.sway_process is not None and self.sway_process.poll() is None:
            logger.warning("Sway is already running")
            return True
            
        try:
            logger.info("Starting Sway...")
            self.sway_process = subprocess.Popen(
                [
                    'sway',
                    '--config', self.sway_config_path
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True
            )
            
            # Check if Sway started successfully
            time.sleep(1)  # Give Sway a moment to start
            if self.sway_process.poll() is not None:
                _, stderr = self.sway_process.communicate()
                logger.error(f"Failed to start Sway: {stderr}")
                return False
                
            logger.info("Sway started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error starting Sway: {e}", exc_info=True)
            return False
    
    def _stop_sway(self) -> None:
        """Stop the Sway window manager gracefully."""
        if self.sway_process is not None and self.sway_process.poll() is None:
            logger.info("Stopping Sway...")
            try:
                # Try to exit Sway gracefully
                self._send_sway_command('exit')
                self.sway_process.wait(timeout=5)
                logger.info("Sway stopped successfully")
            except subprocess.TimeoutExpired:
                logger.warning("Sway did not stop gracefully, terminating...")
                self.sway_process.terminate()
                try:
                    self.sway_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    logger.warning("Force killing Sway...")
                    self.sway_process.kill()
                    self.sway_process.wait()
            except Exception as e:
                logger.error(f"Error stopping Sway: {e}")
            finally:
                self.sway_process = None
    
    def _handle_signal(self, signum, frame) -> None:
        """Handle termination signals.
        
        Args:
            signum: The signal number
            frame: The current stack frame
        """
        signal_name = signal.Signals(signum).name
        logger.info(f"Received signal {signal_name} ({signum}), shutting down...")
        self.running = False
        self._stop_sway()
    
    def run(self) -> None:
        """Run the Sway manager main loop.
        
        This method initializes the Sway configuration, starts Sway, and enters
        a monitoring loop. The loop can be interrupted by a keyboard interrupt
        (Ctrl+C) or a termination signal.
        """
        try:
            # Generate initial Sway config
            if not self._generate_sway_config():
                logger.error("Failed to generate initial Sway configuration")
                return
            
            # Start Sway
            if not self._start_sway():
                logger.error("Failed to start Sway")
                return
            
            logger.info("Sway manager is running. Press Ctrl+C to exit.")
            
            # Main monitoring loop
            self.running = True
            while self.running and self.sway_process:
                # Check if Sway is still running
                if self.sway_process.poll() is not None:
                    logger.error("Sway has terminated unexpectedly")
                    break
                    
        except KeyboardInterrupt:
            logger.info("Shutdown requested by user")
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        finally:
            self._stop_sway()
            logger.info("Sway manager stopped")

def main() -> None:
    """Main entry point for the Sway manager.
    
    Parses command line arguments, validates the configuration file,
    and starts the Sway manager.
    """
    parser = argparse.ArgumentParser(description='Sway Configuration Manager')
    parser.add_argument(
        '-c', '--config',
        required=True,
        help='Path to the configuration file',
        metavar='PATH'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    
    # Verify config file exists and is readable
    config_path = os.path.abspath(args.config)
    try:
        if not os.path.isfile(config_path):
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
            
        if not os.access(config_path, os.R_OK):
            raise PermissionError(f"Cannot read configuration file: {config_path}")
            
        logger.info(f"Starting Sway manager with config: {config_path}")
        
        # Create and run the manager
        manager = SwayManager(config_path)
        manager.run()
        
    except (FileNotFoundError, PermissionError) as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()
