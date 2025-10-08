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

def test_mpv_installation():
    """Test if MPV is installed and working."""
    try:
        result = subprocess.run(
            ['mpv', '--version'],
            capture_output=True,
            text=True,
            check=True
        )
        logger.info("MPV is installed and working:")
        logger.info(result.stdout.split('\n')[0])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error("MPV is not installed or not in PATH")
        logger.error(f"Error: {e}")
        logger.error("Please install MPV using your package manager:")
        logger.error("  Debian/Ubuntu: sudo apt install mpv")
        logger.error("  Fedora: sudo dnf install mpv")
        return False

@dataclass
class PositionConfig:
    """Position configuration for a stream window."""
    x: int = 0
    y: int = 0
    width: int = 640
    height: int = 360

@dataclass
class SwayConfig:
    """Sway-specific window configuration."""
    floating: bool = False
    sticky: bool = True
    workspace: str = None

@dataclass
class StreamConfig:
    """Configuration for a single video stream."""
    id: str
    url: str
    position: PositionConfig = field(default_factory=PositionConfig)
    fps: int = 15
    hwdec: str = 'auto'
    sway: SwayConfig = field(default_factory=SwayConfig)

@dataclass
class SwayLayoutConfig:
    """Sway-specific layout configuration."""
    workspace: str = "streams"

@dataclass
class LayoutConfig:
    """Layout configuration for the stream viewer."""
    screen_width: int = 1920
    screen_height: int = 1080
    margin: int = 10
    max_columns: int = 2
    sway: SwayLayoutConfig = field(default_factory=SwayLayoutConfig)

class StreamViewer:
    """Main application class for managing MPV player instances."""
    
    def __init__(self, config_path: str = None):
        """Initialize the stream viewer.
        
        Args:
            config_path: Path to the configuration file
        """
        self.streams: Dict[str, dict] = {}
        self.mpv_instances: Dict[str, subprocess.Popen] = {}
        self.running = False
        self.layout = LayoutConfig()
        
        # Load configuration if provided
        if config_path and os.path.exists(config_path):
            self.load_config(config_path)
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
    
    def load_config(self, config_path: str) -> bool:
        """Load configuration from a JSON file.
        
        Args:
            config_path: Path to the configuration file
            
        Returns:
            bool: True if configuration was loaded successfully
        """
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Load layout config
            if 'layout' in config:
                self.layout = LayoutConfig(**config['layout'])
            
            # Load stream configs
            if 'streams' in config and isinstance(config['streams'], list):
                for stream_cfg in config['streams']:
                    # Handle nested position and sway configs
                    position_cfg = stream_cfg.pop('position', {})
                    sway_cfg = stream_cfg.pop('sway', {})
                    
                    stream = StreamConfig(
                        **stream_cfg,
                        position=PositionConfig(**position_cfg),
                        sway=SwayConfig(**sway_cfg)
                    )
                    self.streams[stream.id] = stream
                
                # Apply Sway workspace config if specified in layout
                if 'sway' in config.get('layout', {}):
                    self.layout.sway = SwayLayoutConfig(**config['layout']['sway'])
                
                logger.info(f"Loaded configuration for {len(self.streams)} streams")
                return True
                
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
        
        return False
    
    def calculate_layout(self) -> None:
        """Calculate the layout for all streams."""
        if not self.streams:
            return
        
        # Simple grid layout
        num_streams = len(self.streams)
        cols = min(self.layout.max_columns, num_streams)
        rows = (num_streams + cols - 1) // cols
        
        # Calculate stream dimensions with margins
        stream_width = (self.layout.screen_width - (cols + 1) * self.layout.margin) // cols
        stream_height = (self.layout.screen_height - (rows + 1) * self.layout.margin) // rows
        
        # Update stream positions and dimensions
        for i, (stream_id, stream) in enumerate(self.streams.items()):
            row = i // cols
            col = i % cols
            
            stream.x = self.layout.margin + col * (stream_width + self.layout.margin)
            stream.y = self.layout.margin + row * (stream_height + self.layout.margin)
            stream.width = stream_width
            stream.height = stream_height
    
    def start_stream(self, stream: StreamConfig) -> bool:
        """Start an MPV instance for a stream.
        
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
            
            # Build MPV command with Sway support
            cmd = [
                'mpv',
                '--no-config',
                '--no-audio',
                '--no-osc',
                '--no-osd-bar',
                '--no-input-default-bindings',
                '--input-vo-keyboard=no',
                '--title=' + stream.id,
                '--msg-level=all=info',
                '--log-file=' + log_file,
#                '--geometry=' + f'{stream.position.width}x{stream.position.height}+{stream.position.x}+{stream.position.y}',
                '--window-scale=1.0',
                '--no-keepaspect-window',
                '--window-minimized=no',
                '--no-window-dragging',
                stream.url
            ]
            
            # Add hardware decoding if specified
            if stream.hwdec:
                cmd.extend(['--hwdec=' + stream.hwdec])
            
            logger.info(f"Starting MPV with command: {' '.join(cmd)}")
            
            # Start MPV process with error handling
            try:
                # Start MPV process
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    close_fds=True,
                    start_new_session=True
                )
                
                # Give MPV a moment to create the window
                time.sleep(0.5)
                
                # Apply Sway window rules
                # self._apply_sway_rules(stream)
                
                # Check if process started successfully
                time.sleep(1)  # Give it more time to fail
                if process.poll() is not None:
                    _, stderr = process.communicate(timeout=2)
                    logger.error(f"MPV failed to start for {stream.id}. Error: {stderr}")
                    return False
                    
            except subprocess.TimeoutExpired:
                # Process is still running, which is good
                pass
            except Exception as e:
                logger.error(f"Error starting MPV for {stream.id}: {str(e)}")
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
            logger.error(f"Failed to start stream {stream.id}: {str(e)}", exc_info=True)
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
        self.calculate_layout()
        
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
                
    def _apply_sway_rules(self, stream: StreamConfig) -> None:
        try:
            import i3ipc
            sway = i3ipc.Connection()
            
            # Wait a bit longer for the window to appear
            max_attempts = 10
            window_id = None
            
            for attempt in range(max_attempts):
                try:
                    # Get all windows
                    windows = sway.get_tree().leaves()
                    logger.debug(f"Found {len(windows)} windows")
                    
                    for window in windows:
                        # Debug: Print window details
                        logger.debug(f"Window: name='{window.name}' class='{window.window_class}' title='{window.window_title}'")
                        
                        # Try different properties to match the window
                        if (window.name == stream.id or 
                            window.window_title == stream.id or 
                            (hasattr(window, 'window_properties') and 
                            window.window_properties.get('title') == stream.id)):
                            window_id = window.window
                            logger.info(f"Found window for {stream.id}: {window_id}")
                            break
                    
                    if window_id:
                        break
                        
                    time.sleep(0.5)
                    
                except Exception as e:
                    logger.warning(f"Error checking windows (attempt {attempt + 1}): {e}")
                    time.sleep(0.5)
            
            if not window_id:
                logger.warning(f"Could not find window for stream {stream.id}")
                return
            
            # Build and execute Sway commands
            commands = [
                f'[con_id={window_id}] floating enable',
                f'[con_id={window_id}] sticky enable',
                f'[con_id={window_id}] border none',
                f'[con_id={window_id}] move position {stream.position.x} {stream.position.y}',
                f'[con_id={window_id}] resize set {stream.position.width} {stream.position.height}'
            ]
            
            for cmd in commands:
                try:
                    result = sway.command(cmd)
                    logger.debug(f"Command '{cmd}' result: {result}")
                except Exception as e:
                    logger.error(f"Error executing command '{cmd}': {e}")
            
        except ImportError:
            logger.warning("i3ipc not installed. Sway window management disabled.")
        except Exception as e:
            logger.error(f"Error in _apply_sway_rules: {e}", exc_info=True)
    
    def _handle_signal(self, signum, frame) -> None:
        """Handle termination signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop_all()
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
    
    def run(self) -> None:
        """Run the stream viewer."""
        try:
            logger.info("Starting Stream Viewer")
            self.start_all()
            self.monitor()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            self.stop_all()
            logger.info("Stream Viewer stopped")

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Stream Viewer - A simple multi-stream viewer using MPV')
    parser.add_argument('-c', '--config', default='config/streams.json',
                      help='Path to configuration file')
    parser.add_argument('--test', action='store_true',
                      help='Test MPV installation and exit')
    parser.add_argument('--debug', action='store_true',
                      help='Enable debug logging')
    parser.add_argument('--no-sway', action='store_true',
                      help='Disable Sway window management')
    
    args = parser.parse_args()
    
    # Test MPV installation if requested
    if args.test:
        if test_mpv_installation():
            print("MPV test successful!")
            return 0
        return 1
    
    # Enable debug logging if requested
    if args.debug:
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
    
    # Verify MPV is installed
    if not test_mpv_installation():
        logger.error("MPV is required but not found. Please install it first.")
        return 1
    
    # Check for Sway/i3 environment if not explicitly disabled
    use_sway = not args.no_sway and os.environ.get('SWAYSOCK') is not None
    
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