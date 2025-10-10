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
        
        # Load configuration if provided
        if config_path and os.path.exists(config_path):
            self.load_config(config_path)
            self._generate_sway_config()
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
    
    def _generate_sway_config(self) -> None:
        """Generate Sway configuration using the template and append window rules.
        
        Copies the template file and appends window positioning rules for each stream.
        """
        if not self.streams:
            return
            
        config_dir = os.path.dirname(self.sway_config_path)
        os.makedirs(config_dir, exist_ok=True)
        
        # Read the template file if it exists, otherwise use default config
        try:
            with open(self.template_path, 'r') as f:
                config_content = f.read()
        except FileNotFoundError:
            logger.warning(f"Template file not found at {template_path}, using default config")
            config_content = """# Basic Sway Configuration
set $mod Mod1

default_border none
default_floating_border none
focus_follows_mouse no

# Set up outputs
output * {
    bg #000000 solid_color
}
"""
        
        # Generate window positioning rules for each stream
        window_rules = [
            f'''for_window [title="{stream_id}"] {{\n    move position {pos.x} {pos.y}\n}}'''
            for stream_id, stream in self.streams.items()
            for pos in [stream.geometry]
        ]
        
        # Append window rules to the config
        config_content += '\n'.join([''] + window_rules)
        
        # Write the final config
        with open(self.sway_config_path, 'w') as f:
            f.write(config_content)
        
        logger.info(f"Generated Sway configuration at {self.sway_config_path}")

    def load_config(self, config_path: str) -> bool:
        """Load stream configuration from a JSON file.
        
        Args:
            config_path: Path to the configuration file
            
        Returns:
            bool: True if configuration was loaded successfully
        """
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Load stream configs
            if 'streams' in config and isinstance(config['streams'], list):
                for stream_cfg in config['streams']:
                    geometry_cfg = stream_cfg.pop('geometry', {})
                    stream = StreamConfig(
                        id=stream_cfg['id'],
                        url=stream_cfg['url'],
                        geometry=GeometryConfig(**geometry_cfg)
                    )
                    self.streams[stream.id] = stream

                logger.info(f"Loaded configuration for {len(self.streams)} streams")
                return True
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
            
            # Build MPV command
            cmd = [
                'mpv',
                '--no-cache',
                '--no-config',
                '--no-audio',
                '--no-osc',
                '--no-osd-bar',
                '--no-input-default-bindings',
                '--input-vo-keyboard=no',
                f'--title={stream.id}',
                '--msg-level=all=info',
                f'--log-file={log_file}',
                '--window-scale=1.0',
                '--window-minimized=no',
                '--no-window-dragging',
                f'--geometry={stream.geometry.width}x{stream.geometry.height}+{stream.geometry.x}+{stream.geometry.y}',
                stream.url
            ]
            
            logger.info(f"Starting MPV with command: {' '.join(cmd)}")
            
            # Start MPV process with error handling
            try:
                process = subprocess.Popen(
                    cmd,
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