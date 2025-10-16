"""Stream Viewer - A simple multi-stream viewer using MPV for playback."""
import os
import subprocess
import time
import json
import signal
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import psutil

# Configure logging
def setup_logging():
    """Set up logging configuration."""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, 'stream_viewer.log')
    
    # Clear previous log file
    with open(log_file, 'w'):
        pass
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Console handler (INFO level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    
    # File handler (DEBUG level)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logging.getLogger('stream_viewer')

# Initialize logging
logger = setup_logging()

@dataclass
class GeometryConfig:
    x: int
    y: int
    width: int
    height: int

@dataclass
class StreamConfig:
    id: str
    url: str
    geometry: GeometryConfig = field(default_factory=GeometryConfig)

class StreamViewer:
    """Manages multiple MPV streams with Sway window management."""
    
    def __init__(self, config_path: str = None):
        self.streams: Dict[str, dict] = {}
        self.mpv_instances: Dict[str, subprocess.Popen] = {}
        self.running = False
        self.config_path = config_path
        # Store paths for template and generated config
        config_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')
        self.template_path = os.path.join(config_dir, 'sway_config_template.in')
        self.sway_config_path = os.path.join(config_dir, 'sway_config.in')
        self.sway_process = None
        
        # Load configuration if provided
        if config_path and os.path.exists(config_path):
            if not self.load_config(config_path):
                logger.error("Failed to load configuration")
            elif not self._generate_sway_config():
                logger.error("Failed to generate Sway configuration")
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
    
    def _generate_sway_config(self) -> bool:
        """Generate Sway configuration using the template and append window rules.
        
        Returns:
            bool: True if config was generated successfully, False otherwise
            
        The method will:
        1. Create the config directory if it doesn't exist
        2. Always generate a new config file from the template
        3. Return False if no template file is found
        """
        if not self.streams:
            logger.warning("No streams configured, skipping Sway config generation")
            return False
        
        config_dir = os.path.dirname(self.sway_config_path)
        os.makedirs(config_dir, exist_ok=True)
        
        # Read template file
        if not os.path.exists(self.template_path):
            logger.error(f"Template file not found: {self.template_path}")
            return False
            
        try:
            with open(self.template_path, 'r') as f:
                config_content = f.read()
            logger.debug("Using template from file")
        except Exception as e:
            logger.error(f"Failed to read template file: {e}")
            return False
        
        # Generate window positioning rules for each stream
        window_rules = [
            f'''for_window [title="{stream_id}"] {{\n    move position {pos.x} {pos.y}\n    resize set width {pos.width} height {pos.height}\n}}'''
            for stream_id, stream in self.streams.items()
            for pos in [stream.geometry]
        ]
        
        # Append window rules to the config
        new_config = config_content + '\n'.join([''] + window_rules)
        
        try:
            # Create a temporary file first
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', delete=False, dir=config_dir) as temp_file:
                temp_file.write(new_config)
                temp_path = temp_file.name
            
            # Atomically replace the old config
            os.replace(temp_path, self.sway_config_path)
            logger.info(f"Generated new Sway configuration at {self.sway_config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to write Sway config: {e}")
            # Clean up temp file if it exists
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
            return False

    def load_config(self, config_path: str) -> bool:
        """Load stream configuration from a JSON file.
        
        Args:
            config_path: Path to the configuration file
            
        Returns:
            bool: True if configuration was loaded successfully
        """
        if not os.path.exists(config_path):
            logger.error(f"Configuration file not found: {config_path}")
            return False
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Load stream configs
            if 'streams' in config and isinstance(config['streams'], list):
                old_streams = self.streams.copy()
                self.streams = {}
                
                for stream_cfg in config['streams']:
                    try:
                        geometry_cfg = stream_cfg.pop('geometry', {})
                        stream = StreamConfig(
                            id=stream_cfg['id'],
                            url=stream_cfg['url'],
                            geometry=GeometryConfig(**geometry_cfg)
                        )
                        self.streams[stream.id] = stream
                    except Exception as e:
                        logger.error(f"Error loading stream config {stream_cfg.get('id', 'unknown')}: {e}")
                        continue

                logger.info(f"Loaded configuration for {len(self.streams)} streams")
                
                # Regenerate Sway config if streams changed
                if old_streams != self.streams:
                    logger.info("Stream configuration changed, regenerating Sway config...")
                    return self._generate_sway_config(force_regenerate=True)
                
                return True
                
            logger.error("No valid 'streams' array found in configuration")
            return False
            
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return False

    def start_stream(self, stream: StreamConfig) -> bool:
        """Start a single stream.
        
        Args:
            stream: Stream configuration
            
        Returns:
            bool: True if the stream was started successfully
        """
        if stream.id in self.mpv_instances:
            process = self.mpv_instances[stream.id]
            if process.poll() is None:  # Process is still running
                logger.warning(f"Stream {stream.id} is already running")
                return False
            else:  # Process exists but is dead
                logger.warning(f"Removing dead process for {stream.id}")
                del self.mpv_instances[stream.id]
        
        try:
            # Create log directory if it doesn't exist
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f'mpv_{stream.id}.log')
            
            # Build MPV command with Wayland support
            cmd = [
                'mpv',
                '--no-cache',
                '--no-config',
                '--no-audio',
                '--no-osc',
                '--no-osd-bar',
                '--no-input-default-bindings',
                '--gpu-context=wayland',  # Force Wayland backend
                '--gpu-api=vulkan',       # Use Vulkan for better Wayland support
                '--input-vo-keyboard=no',
                f'--title={stream.id}',
                '--msg-level=all=info',
                f'--log-file={log_file}',
                '--window-scale=1.0',
                '--window-minimized=no',
                '--no-window-dragging',
                f'--geometry={stream.geometry.width}x{stream.geometry.height}+{stream.geometry.x}+{stream.geometry.y}',
                '--force-window=immediate',  # Force window creation immediately
                stream.url
            ]
            
            logger.info(f"Starting MPV with command: {' '.join(cmd)}")
            
            # Set up environment for Wayland
            env = os.environ.copy()
            env['SDL_VIDEODRIVER'] = 'wayland'
            env['QT_QPA_PLATFORM'] = 'wayland'
            env['GDK_BACKEND'] = 'wayland'
            env['MOZ_ENABLE_WAYLAND'] = '1'
            env['_JAVA_AWT_WM_NONREPARENTING'] = '1'
            env['CLUTTER_BACKEND'] = 'wayland'
            
            # Start MPV process with error handling and Wayland environment
            try:
                process = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    close_fds=True,
                    start_new_session=True
                )
                
                # Wait for window to be created
                time.sleep(1.0)
                
                # Check if process started successfully
                if process.poll() is not None:
                    _, stderr = process.communicate(timeout=2)
                    logger.error(f"MPV failed to start for {stream.id}. Error: {stderr}")
                    return False
                    
            except subprocess.TimeoutExpired:
                # Process is still running, which is good
                pass
                
            except Exception as e:
                logger.error(f"Error starting MPV for {stream.id}: {e}")
                return False
            
            self.mpv_instances[stream.id] = process
            logger.info(f"Started stream {stream.id} (PID: {process.pid})")
            
            # Set up a thread to monitor the process output
            def log_output(process, stream_id):
                while process.poll() is None:
                    line = process.stderr.readline()
                    if line:
                        logger.debug(f"MPV {stream_id}: {line.strip()}")
            
            import threading
            t = threading.Thread(
                target=log_output,
                args=(process, stream.id),
                daemon=True
            )
            t.start()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start stream {stream.id}", exc_info=True)
            return False
    
    def stop_stream(self, stream_id: str) -> bool:
        """Stop a running MPV instance.
        
        Args:
            stream_id: ID of the stream to stop
            
        Returns:
            bool: True if the stream was stopped successfully
        """
        if stream_id not in self.mpv_instances:
            logger.warning(f"Stream {stream_id} is not running")
            return False
        
        try:
            process = self.mpv_instances[stream_id]
            
            # Try to terminate gracefully first
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate
                process.kill()
                process.wait()
            
            del self.mpv_instances[stream_id]
            logger.info(f"Stopped stream {stream_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error stopping stream {stream_id}: {e}")
            return False
    
    def start_all(self) -> None:
        """Start all configured streams."""
        if not self.streams:
            logger.warning("No streams configured")
            return
        
        self.running = True
        
        # Start each stream with a small delay between them
        for i, (stream_id, stream) in enumerate(self.streams.items()):
            if self.start_stream(stream):
                time.sleep(0.5)  # Small delay to stagger stream starts
    
    def stop_all(self) -> None:
        """Stop all running streams."""
        self.running = False
        
        # Stop all MPV instances
        for stream_id in list(self.mpv_instances.keys()):
            self.stop_stream(stream_id)
                
    def _handle_signal(self, signum, frame) -> None:
        """Handle termination signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop_all()
        self._stop_sway()

    def monitor(self) -> None:
        """Monitor and restart failed streams."""
        while self.running:
            time.sleep(5)  # Check every 5 seconds
            
            # Check for dead processes
            dead_streams = []
            for stream_id, process in list(self.mpv_instances.items()):
                if process.poll() is not None:  # Process has terminated
                    logger.warning(f"Stream {stream_id} died with code {process.returncode}")
                    dead_streams.append(stream_id)
            
            # Restart dead streams
            for stream_id in dead_streams:
                if stream_id in self.streams and self.running:
                    logger.info(f"Restarting stream {stream_id}")
                    self.start_stream(self.streams[stream_id])
    
    def _start_sway(self) -> bool:
        """Start Sway with the generated configuration.
        
        Returns:
            bool: True if Sway started successfully
        """
        if not os.path.exists(self.sway_config_path):
            logger.error(f"Sway config file not found at {self.sway_config_path}")
            logger.error("This usually means either:")
            logger.error("1. No streams are configured in your config file")
            logger.error("2. The template file is missing at config/sway_config_template.in")
            logger.error("3. There was an error generating the config")
            return False
            
        try:
            # Start Sway with the generated config
            cmd = ['sway', '--config', self.sway_config_path]
            logger.info(f"Starting Sway with command: {' '.join(cmd)}")
            
            self.sway_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True
            )
            
            # Wait a moment for Sway to start
            time.sleep(2)
            
            # Get the default Sway socket path
            user_id = os.getuid()
            sway_pid = self.sway_process.pid
            self._swaysock_path = f"/run/user/{user_id}/sway-ipc.{user_id}.{sway_pid}.sock"
            
            if not os.path.exists(self._swaysock_path):
                logger.error(f"Could not find Sway socket at {self._swaysock_path}")
                return False
                
            logger.info("Sway started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error starting Sway: {e}", exc_info=True)
            return False
    
    def _stop_sway(self) -> None:
        """Stop the Sway process if it's running."""
        if self.sway_process and self.sway_process.poll() is None:
            try:
                logger.info("Stopping Sway...")
                # Use swaymsg to exit gracefully
                if hasattr(self, '_swaysock_path') and os.path.exists(self._swaysock_path):
                    try:
                        subprocess.run(
                            ['swaymsg', 'exit'],
                            timeout=5,
                            check=True
                        )
                    except subprocess.TimeoutExpired:
                        logger.warning("Sway did not exit gracefully, forcing termination")
                        self.sway_process.terminate()
                    except Exception as e:
                        logger.error(f"Error stopping Sway: {e}")
                
                # Wait for process to terminate
                try:
                    self.sway_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning("Sway did not terminate, killing process")
                    self.sway_process.kill()
                
                logger.info("Sway stopped")
                
            except Exception as e:
                logger.error(f"Error stopping Sway: {e}", exc_info=True)

    def run(self) -> None:
        """Run the stream viewer."""
        try:
            logger.info("Starting Stream Viewer")
            
            # First generate Sway config if we have streams
            if self.streams and not self._generate_sway_config():
                logger.error("Failed to generate Sway configuration, exiting...")
                return
                
            # Then start Sway
            if not self._start_sway():
                logger.error("Failed to start Sway, exiting...")
                return
                
            # Wait 5 seconds to ensure Sway is fully initialized
            logger.info("Waiting 5 seconds for Sway to initialize...")
            time.sleep(5)
            
            # Then start the streams
            logger.info("Starting all streams...")
            self.start_all()
            self.monitor()
            
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            self.stop_all()
            self._stop_sway()
            logger.info("Stream Viewer stopped")

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Stream Viewer - A simple multi-stream viewer using MPV')
    parser.add_argument('-c', '--config', default='config/streams.json',
                      help='Path to configuration file')
    parser.add_argument('--debug', action='store_true',
                      help='Enable debug logging')
    args = parser.parse_args()
    
    # Enable debug logging if requested
    if args.debug:
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
    
    # Run the viewer
    try:
        viewer = StreamViewer(args.config)
        viewer.run()
        return 0
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        return 1
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        return 0

if __name__ == '__main__':
    main()