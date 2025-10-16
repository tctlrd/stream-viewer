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
    """Manages Sway window manager configuration and operations.
    
    This class provides methods to generate Sway configurations, reload Sway,
    and manage the Sway session programmatically.
    """
    
    def __init__(self, config_path: str):
        """Initialize the Sway manager.
        
        Args:
            config_path: Path to the configuration file
            
        Raises:
            FileNotFoundError: If the config directory cannot be created
        """
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
    
    def _get_sway_socket(self) -> str:
        """Get the path to the Sway IPC socket.
        
        Returns:
            str: Path to the Sway socket
            
        Raises:
            RuntimeError: If the Sway socket cannot be determined
        """
        # Try to get the socket from SWAYSOCK environment variable
        if 'SWAYSOCK' in os.environ:
            socket_path = os.environ['SWAYSOCK']
            if os.path.exists(socket_path):
                return socket_path
            logger.warning(f"SWAYSOCK environment variable set but socket not found: {socket_path}")
        
        try:
            # Try to get the socket from the default location
            user_id = os.getuid()
            result = subprocess.run(
                ['pidof', 'sway'],
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode != 0:
                raise RuntimeError("Sway is not running")
                
            sway_pid = result.stdout.strip()
            if not sway_pid:
                raise RuntimeError("Could not determine Sway process ID")
                
            socket_path = f'/run/user/{user_id}/sway-ipc.{user_id}.{sway_pid}.sock'
            
            if not os.path.exists(socket_path):
                raise RuntimeError(f"Sway socket not found at {socket_path}")
                
            return socket_path
            
        except subprocess.SubprocessError as e:
            logger.error(f"Failed to get Sway process ID: {e}")
            raise RuntimeError("Could not determine Sway process ID") from e
        except Exception as e:
            logger.error(f"Failed to get Sway socket: {e}")
    
    def _send_sway_command(self, command: str) -> bool:
        """Send a command to Sway via swaymsg.
        
        Args:
            command: The command to send (e.g., 'reload', 'restart')
                
        Returns:
            bool: True if the command was sent successfully, False otherwise
            
        Example:
            >>> manager = SwayManager('/path/to/config')
            >>> manager._send_sway_command('reload')
            True
        """
        try:
            socket_path = self._get_sway_socket()
            cmd = [
                'swaymsg',
                '--socket', socket_path,
                command
            ]
            
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10  # Add timeout to prevent hanging
            )
            
            if result.stderr:
                logger.warning(f"Sway command output: {result.stderr.strip()}")
                
            logger.debug(f"Sway command '{command}' executed successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Sway command failed with code {e.returncode}: {e.stderr.strip()}")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"Sway command timed out: {command}")
            return False
        except Exception as e:
            logger.error(f"Error sending Sway command '{command}': {e}", exc_info=True)
            return False
    
    def _generate_sway_config(self) -> bool:
        """Generate Sway configuration using the template.
        
        This method reads the template file and generates a Sway configuration file.
        The generated file will be saved to the path specified by self.sway_config_path.
        
        Returns:
            bool: True if config was generated successfully, False otherwise
            
        Raises:
            FileNotFoundError: If the template file does not exist
            IOError: If there is an error reading or writing the files
        """
        logger.info(f"Generating Sway configuration from template: {self.template_path}")
        
        if not os.path.exists(self.template_path):
            error_msg = f"Sway config template not found at {self.template_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
            
        try:
            # Read template with explicit encoding
            with open(self.template_path, 'r', encoding='utf-8') as f:
                template = f.read()
            
            # Ensure the output directory exists
            os.makedirs(os.path.dirname(self.sway_config_path), exist_ok=True)
            
            # Write config file with explicit encoding
            with open(self.sway_config_path, 'w', encoding='utf-8') as f:
                f.write(template)
            
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
    
    def reload_sway(self) -> bool:
        """Reload the Sway configuration.
        
        This will cause Sway to reload its configuration file and apply any changes.
        
        Returns:
            bool: True if the reload command was sent successfully, False otherwise
            
        Example:
            >>> manager = SwayManager('/path/to/config')
            >>> if manager.reload_sway():
            ...     print("Sway configuration reloaded successfully")
        """
        logger.info("Reloading Sway configuration...")
        return self._send_sway_command('reload')
    
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
                    '--config', self.sway_config_path,
                    '--debug'
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
                
                try:
                    # Check for config file changes
                    current_mtime = os.path.getmtime(self.template_path)
                    if hasattr(self, '_last_mtime') and current_mtime > self._last_mtime:
                        logger.info("Detected config file changes, reloading...")
                        if self._generate_sway_config():
                            self.reload_sway()
                    self._last_mtime = current_mtime
                    
                    # Small sleep to prevent high CPU usage
                    time.sleep(1)
                    
                except (IOError, OSError) as e:
                    logger.warning(f"Error checking config file: {e}")
                    time.sleep(5)  # Wait longer on error
                except Exception as e:
                    logger.error(f"Unexpected error in monitoring loop: {e}", exc_info=True)
                    time.sleep(5)  # Wait longer on error
                    
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
